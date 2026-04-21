# Task Completion Checklist

When finishing a coding task in this project:

1. **Run the dev server** to verify the feature works end-to-end:
   ```bash
   uvicorn app.main:app --reload
   ```

2. **Run migrations** if any model changes were made:
   ```bash
   alembic revision --autogenerate -m "describe change"
   alembic upgrade head
   ```

3. **No automated test suite** exists in this project — manual testing via browser/curl is required.

4. **No linting/formatting tool configured** — ensure code is consistent with existing style (see style_and_conventions.md).

5. **Update requirements.txt** if new packages were added:
   ```bash
   pip freeze > requirements.txt
   # Or manually add the specific package with pinned version
   ```

6. **Check logs** for runtime errors:
   ```bash
   journalctl -u vozvrat -f   # production
   # or check uvicorn stdout in dev
   ```
