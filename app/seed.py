"""
Seed script: creates initial users, suppliers, and demo refunds.
Usage: python -m app.seed
"""
import asyncio
import logging
from decimal import Decimal
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.supplier import Supplier
from app.models.refund import Refund, RefundStatus, RefundSource
from app.models.refund_item import RefundItem
from app.services.auth import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def seed(db: AsyncSession) -> None:
    logger.info("Starting seed...")

    # --- Users ---
    users_data = [
        {
            "email": "admin@vozvrat.ru",
            "password": "admin123",
            "full_name": "Администратор",
            "role": UserRole.admin,
        },
        {
            "email": "staff@vozvrat.ru",
            "password": "staff123",
            "full_name": "Александр Сотрудников",
            "role": UserRole.staff,
        },
        {
            "email": "client@vozvrat.ru",
            "password": "client123",
            "full_name": "ИП ТехноМир",
            "role": UserRole.client,
        },
    ]

    created_users = {}
    for udata in users_data:
        existing = await db.execute(select(User).where(User.email == udata["email"]))
        user = existing.scalar_one_or_none()
        if not user:
            user = User(
                email=udata["email"],
                password_hash=hash_password(udata["password"]),
                full_name=udata["full_name"],
                role=udata["role"],
            )
            db.add(user)
            await db.flush()
            logger.info(f"Created user: {udata['email']} ({udata['role'].value})")
        else:
            logger.info(f"User already exists: {udata['email']}")
        created_users[udata["email"]] = user

    # --- Suppliers ---
    suppliers_data = [
        {"name": "Bosch Russia", "email": "bosch-support@mail.ru"},
        {"name": "ООО «Запчасть-Опт»", "email": "orders@zapchast-opt.ru"},
    ]

    created_suppliers = {}
    for sdata in suppliers_data:
        existing = await db.execute(select(Supplier).where(Supplier.name == sdata["name"]))
        supplier = existing.scalar_one_or_none()
        if not supplier:
            supplier = Supplier(name=sdata["name"], email=sdata["email"])
            db.add(supplier)
            await db.flush()
            logger.info(f"Created supplier: {sdata['name']}")
        else:
            logger.info(f"Supplier already exists: {sdata['name']}")
        created_suppliers[sdata["name"]] = supplier

    await db.flush()

    # Check if demo refunds already exist
    existing_count = (await db.execute(select(Refund))).scalars().all()
    if existing_count:
        logger.info(f"Refunds already exist ({len(existing_count)} found), skipping demo refunds")
        await db.commit()
        return

    # --- Demo Refunds ---
    admin_user = created_users["admin@vozvrat.ru"]
    staff_user = created_users["staff@vozvrat.ru"]
    client_user = created_users["client@vozvrat.ru"]
    bosch = created_suppliers["Bosch Russia"]
    zapchast = created_suppliers["ООО «Запчасть-Опт»"]

    refunds_data = [
        {
            "display_id": "#10001",
            "status": RefundStatus.received,
            "source": RefundSource.email,
            "client_name": "ИП ТехноМир",
            "client_user_id": client_user.id,
            "supplier_id": bosch.id,
            "order_id": "#ORD-55441",
            "reason": "Брак заводской, не работает с первого дня",
            "created_by_id": None,
            "email_subject": "Возврат товара Bosch",
            "email_from": "technomir@mail.ru",
            "items": [
                {"article": "BOSCH-199-22", "brand": "Bosch", "quantity": 2, "price": Decimal("3500.00"), "description": "Дрель перфоратор"},
            ],
        },
        {
            "display_id": "#10002",
            "status": RefundStatus.in_progress,
            "source": RefundSource.manual,
            "client_name": "ООО СтройМаркет",
            "client_user_id": None,
            "supplier_id": bosch.id,
            "order_id": "#ORD-55442",
            "reason": "Несоответствие комплектации",
            "created_by_id": staff_user.id,
            "items": [
                {"article": "BOSCH-GBH-2-26", "brand": "Bosch", "quantity": 1, "price": Decimal("12500.00"), "description": "Перфоратор GBH 2-26"},
                {"article": "BOSCH-ACC-KIT", "brand": "Bosch", "quantity": 1, "price": Decimal("1800.00"), "description": "Набор аксессуаров"},
            ],
        },
        {
            "display_id": "#10003",
            "status": RefundStatus.approved,
            "source": RefundSource.manual,
            "client_name": "ИП Самойлов А.В.",
            "client_user_id": None,
            "supplier_id": zapchast.id,
            "order_id": "#ORD-55300",
            "reason": None,
            "created_by_id": staff_user.id,
            "items": [
                {"article": "ZO-FILTER-12", "brand": "Mann", "quantity": 5, "price": Decimal("450.00"), "description": "Фильтр масляный"},
            ],
        },
        {
            "display_id": "#10004",
            "status": RefundStatus.sent_to_supplier,
            "source": RefundSource.manual,
            "client_name": "ООО Авто-Детали",
            "client_user_id": None,
            "supplier_id": zapchast.id,
            "order_id": "#ORD-55001",
            "reason": "Пришел не тот размер",
            "created_by_id": admin_user.id,
            "items": [
                {"article": "BELT-R65-220", "brand": None, "quantity": 10, "price": Decimal("320.00"), "description": "Ремень приводной"},
            ],
        },
        {
            "display_id": "#10005",
            "status": RefundStatus.archive,
            "source": RefundSource.email,
            "client_name": "ИП ТехноМир",
            "client_user_id": client_user.id,
            "supplier_id": None,
            "order_id": "#ORD-54000",
            "reason": "Возврат по гарантии",
            "created_by_id": None,
            "email_subject": "Гарантийный возврат",
            "email_from": "technomir@mail.ru",
            "items": [
                {"article": "SAMSUNG-TV-55", "brand": "Samsung", "quantity": 1, "price": Decimal("45000.00"), "description": "Телевизор 55\""},
            ],
        },
    ]

    for rdata in refunds_data:
        items_data = rdata.pop("items", [])
        refund = Refund(**rdata)
        db.add(refund)
        await db.flush()

        for idata in items_data:
            item = RefundItem(refund_id=refund.id, **idata)
            db.add(item)

        logger.info(f"Created refund {rdata['display_id']} (status={rdata['status'].value})")

    await db.commit()
    logger.info("Seed completed successfully!")


async def main():
    async with AsyncSessionLocal() as db:
        await seed(db)


if __name__ == "__main__":
    asyncio.run(main())
