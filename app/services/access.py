"""Ограничение видимости по менеджеру.

Менеджер (сотрудник, role=staff) видит клиентов, которые НЕ закреплены за другим
сотрудником: своих (manager_id = его id) и «ничьих» (manager_id пуст). Записи без
клиента (client_user_id пуст) тоже видны всем сотрудникам. Скрыты только записи,
чей клиент закреплён за другим сотрудником. Администратор (role=admin) видит всё.

Сотрудник может сам назначать себя менеджером «ничьего» клиента и открепляться от
своих — через страницу «Клиенты» (см. app/routers/users.py).
"""
from typing import Optional
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


def is_manager_scoped(user: User) -> bool:
    """True, если к пользователю нужно применять фильтр «только свои клиенты»."""
    return user.role == UserRole.staff


def managed_client_ids_subquery(user: User):
    """Скалярный подзапрос с id клиентов, закреплённых за этим сотрудником."""
    return select(User.id).where(User.manager_id == user.id).scalar_subquery()


def _staff_visible_client_condition(user: User, client_fk_column):
    """Условие видимости записи для сотрудника по её клиенту.

    Видно, если у записи нет клиента, либо клиент не закреплён за другим
    сотрудником (менеджер пуст или это текущий сотрудник).
    """
    visible_ids = select(User.id).where(
        or_(User.manager_id == user.id, User.manager_id.is_(None))
    ).scalar_subquery()
    return or_(client_fk_column.is_(None), client_fk_column.in_(visible_ids))


def restrict_by_manager(query, user: User, client_fk_column):
    """Ограничить запрос возвратов/запросов видимыми сотруднику клиентами.

    Для admin/client запрос не меняется (их фильтрация задаётся отдельно).
    """
    if is_manager_scoped(user):
        query = query.where(_staff_visible_client_condition(user, client_fk_column))
    return query


def restrict_requests_by_manager(query, user: User):
    """Ограничить запросы (Request) для сотрудника.

    Помимо запросов видимых клиентов сотрудник видит запросы, где он сам является
    автором или исполнителем — иначе он потерял бы назначенные ему задачи.
    Для admin/client запрос не меняется.
    """
    if not is_manager_scoped(user):
        return query
    # Импортируем здесь, чтобы не создавать циклическую зависимость на уровне модуля.
    from app.models.request import Request as RequestModel

    return query.where(
        or_(
            _staff_visible_client_condition(user, RequestModel.client_user_id),
            RequestModel.created_by_id == user.id,
            RequestModel.executor_id == user.id,
        )
    )


def staff_refund_scope_exists(user: User):
    """EXISTS-условие: возврат сообщения виден этому сотруднику.

    Возвращает None, если ограничение не нужно (admin/client).
    """
    if not is_manager_scoped(user):
        return None
    from sqlalchemy import and_, exists
    from app.models.refund import Refund
    from app.models.message import Message
    return exists().where(
        and_(
            Refund.id == Message.refund_id,
            _staff_visible_client_condition(user, Refund.client_user_id),
        )
    )


def staff_request_scope_exists(user: User):
    """EXISTS-условие: запрос сообщения виден этому сотруднику.

    Возвращает None, если ограничение не нужно (admin/client).
    """
    if not is_manager_scoped(user):
        return None
    from sqlalchemy import and_, exists
    from app.models.request import Request as RequestModel
    from app.models.message import Message
    return exists().where(
        and_(
            RequestModel.id == Message.request_id,
            or_(
                _staff_visible_client_condition(user, RequestModel.client_user_id),
                RequestModel.created_by_id == user.id,
                RequestModel.executor_id == user.id,
            ),
        )
    )


def clients_for_user_query(user: User):
    """Запрос активных клиентов, видимых пользователю.

    Сотрудник видит своих и «ничьих» клиентов; администратор — всех.
    """
    query = select(User).where(User.role == UserRole.client, User.is_active == True)
    if is_manager_scoped(user):
        query = query.where(or_(User.manager_id == user.id, User.manager_id.is_(None)))
    return query.order_by(User.full_name)


async def staff_can_access_request(db: AsyncSession, user: User, req) -> bool:
    """Может ли сотрудник открыть запрос (видимый клиент / его задача)."""
    if not is_manager_scoped(user):
        return True
    if req.created_by_id == user.id or req.executor_id == user.id:
        return True
    return await staff_can_access_client(db, user, req.client_user_id)


async def staff_can_access_client(db: AsyncSession, user: User, client_user_id: Optional[int]) -> bool:
    """Может ли сотрудник работать с указанным клиентом.

    Доступен, если клиента нет (None) или он не закреплён за другим сотрудником
    (менеджер пуст либо это текущий сотрудник).
    """
    if not is_manager_scoped(user):
        return True
    if not client_user_id:
        return True
    result = await db.execute(
        select(User.manager_id).where(User.id == client_user_id)
    )
    row = result.first()
    if row is None:
        # Клиента нет в базе — считаем недоступным.
        return False
    manager_id = row[0]
    return manager_id is None or manager_id == user.id
