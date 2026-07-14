import asyncio
import logging
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tables", tags=["tables"])


def _patch_openpyxl_noneset():
    """Some xlsx files contain invalid 'pane' attribute values that openpyxl rejects.
    The error comes from NoneSet.__set__ (a descriptor), not __init__.
    Patch NoneSet.__set__ once to silently coerce unknown values to None.
    (Same workaround as app/routers/refunds.py.)"""
    import openpyxl.descriptors.base as _openpyxl_desc

    if not getattr(_openpyxl_desc, "_noneset_patched", False):
        _orig_noneset_set = _openpyxl_desc.NoneSet.__set__

        def _safe_noneset_set(self, instance, value):
            if value is not None and value != "none" and value not in self.values:
                value = None
            _orig_noneset_set(self, instance, value)

        _openpyxl_desc.NoneSet.__set__ = _safe_noneset_set
        _openpyxl_desc._noneset_patched = True


def _norm(value) -> str:
    """Normalize an article/header value for matching: trim + upper-case."""
    if value is None:
        return ""
    return str(value).strip().upper()


# Заголовки колонок с кодами (как в файле-справочнике)
TNVED_HEADER = "код ТЭНВД"
OKPD_HEADER = "код ОКПД 2"


def _find_header_row(ws, wanted_norms, max_scan=25):
    """Find the first row (within max_scan) that contains any of the wanted header
    names. Returns (row_index, {norm_name: column_index}) or (None, {})."""
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_scan, values_only=True), start=1):
        col_map = {}
        for col_idx, cell in enumerate(row):
            n = _norm(cell)
            if n:
                col_map.setdefault(n, col_idx)
        if any(w in col_map for w in wanted_norms):
            return row_idx, col_map
    return None, {}


def _build_codes_map(codes_bytes: bytes) -> dict:
    """Read the codes reference file into {norm(article): (tnved, okpd2)}."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(BytesIO(codes_bytes), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Не удалось открыть файл «таблица с кодами». Убедитесь, что это корректный XLS/XLSX.",
        )

    ws = wb.active
    art_n = _norm("артикул")
    tnved_n = _norm(TNVED_HEADER)
    okpd_n = _norm(OKPD_HEADER)

    header_row, col_map = _find_header_row(ws, {art_n})
    if header_row is None or art_n not in col_map or tnved_n not in col_map or okpd_n not in col_map:
        wb.close()
        raise HTTPException(
            status_code=400,
            detail="В файле «таблица с кодами» не найдены колонки «артикул», «код ТЭНВД», «код ОКПД 2».",
        )

    art_col = col_map[art_n]
    tnved_col = col_map[tnved_n]
    okpd_col = col_map[okpd_n]

    codes_map: dict = {}
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if art_col >= len(row):
            continue
        key = _norm(row[art_col])
        if not key:
            continue
        tnved = row[tnved_col] if tnved_col < len(row) else None
        okpd = row[okpd_col] if okpd_col < len(row) else None
        codes_map[key] = (tnved, okpd)

    wb.close()
    return codes_map


def _enrich_price(price_bytes: bytes, codes_map: dict):
    """Load the price workbook, append two code columns, fill matched rows.
    Returns (xlsx_bytes, matched, total)."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(BytesIO(price_bytes), data_only=False)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Не удалось открыть файл «Прайс». Убедитесь, что это корректный XLS/XLSX.",
        )

    ws = wb.active
    catalog_n = _norm("Каталожный номер")
    header_row, col_map = _find_header_row(ws, {catalog_n})
    if header_row is None or catalog_n not in col_map:
        wb.close()
        raise HTTPException(
            status_code=400,
            detail="В файле «Прайс» не найдена колонка «Каталожный номер».",
        )

    art_col = col_map[catalog_n]  # 0-based
    # Первые свободные колонки справа (1-based для openpyxl cell)
    tnved_col = ws.max_column + 1
    okpd_col = ws.max_column + 2

    ws.cell(row=header_row, column=tnved_col, value=TNVED_HEADER)
    ws.cell(row=header_row, column=okpd_col, value=OKPD_HEADER)

    matched = 0
    total = 0
    for row_idx in range(header_row + 1, ws.max_row + 1):
        art_cell = ws.cell(row=row_idx, column=art_col + 1)
        key = _norm(art_cell.value)
        if not key:
            continue
        total += 1
        found = codes_map.get(key)
        if found is not None:
            matched += 1
            ws.cell(row=row_idx, column=tnved_col, value=found[0])
            ws.cell(row=row_idx, column=okpd_col, value=found[1])

    out = BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue(), matched, total


@router.post("/enrich")
async def enrich_price_with_codes(
    request: Request,
    price_file: UploadFile = File(...),
    codes_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Найти артикулы «Прайса» в «таблице с кодами», дописать колонки
    «код ТЭНВД» и «код ОКПД 2», вернуть готовый файл для скачивания."""
    user = await get_current_user(request, db)
    if user.role.value not in ("admin", "staff"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    _patch_openpyxl_noneset()

    price_bytes = await price_file.read()
    codes_bytes = await codes_file.read()
    if not price_bytes or not codes_bytes:
        raise HTTPException(status_code=400, detail="Оба файла обязательны для загрузки.")

    loop = asyncio.get_event_loop()

    def _process():
        codes_map = _build_codes_map(codes_bytes)
        return _enrich_price(price_bytes, codes_map)

    xlsx_data, matched, total = await loop.run_in_executor(None, _process)

    base_name = (price_file.filename or "прайс").rsplit(".", 1)[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{base_name}_с_кодами_{ts}.xlsx"

    from urllib.parse import quote
    ascii_fallback = "price_with_codes.xlsx"
    cd = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"

    return Response(
        content=xlsx_data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": cd,
            "X-Matched-Count": str(matched),
            "X-Total-Count": str(total),
        },
    )
