"""Ограничение видимости по менеджеру.

Менеджер (сотрудник, role=staff) видит только своих клиентов и их возвраты/
запросы. Клиент считается «своим», если у него проставлен manager_id = id
сотрудника. Администратор (role=admin) видит всё.

Записи без назначенного клиента (client_user_id IS NULL) или клиента без
менеджера сотруднику не показываются — их видит только администратор.
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


def is_manager_scoped(user: User) -> bool:
    """True, если к пользователю нужно применять фильтр «только свои клиенты»."""
    return user.role == UserRole.staff


def managed_client_ids_subquery(user: User):
    """Скалярный подзапрос с id клиентов, закреплённых за этим сотрудником."""
    return select(User.id).where(User.manager_id == user.id).scalar_subquery()


def restrict_by_manager(query, user: User, client_fk_column):
    """Ограничить запрос возвратов/запросов клиентами сотрудника.

    Для admin/client запрос не меняется (их фильтрация задаётся отдельно).
    """
    if is_manager_scoped(user):
        query = query.where(client_fk_column.in_(managed_client_ids_subquery(user)))
    return query


def restrict_requests_by_manager(query, user: User):
    """Ограничить запросы (Request) для сотрудника.

    Помимо запросов своих клиентов сотрудник видит запросы, где он сам является
    заявителем, автором или исполнителем — иначе он потерял бы собственные
    запросы и назначенные ему задачи. Для admin/client запрос не меняется.
    """
    if not is_manager_scoped(user):
        return query
    # Импортируем здесь, чтобы не создавать циклическую зависимость на уровне модуля.
    from sqlalchemy import or_
    from app.models.request import Request as RequestModel

    return query.where(
        or_(
            RequestModel.client_user_id.in_(managed_client_ids_subquery(user)),
            RequestModel.client_user_id == user.id,
            RequestModel.created_by_id == user.id,
            RequestModel.executor_id == user.id,
        )
    )


def staff_refund_scope_exists(user: User):
    """EXISTS-условие: возврат сообщения принадлежит клиенту этого сотрудника.

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
            Refund.client_user_id.in_(managed_client_ids_subquery(user)),
        )
    )


def staff_request_scope_exists(user: User):
    """EXISTS-условие: запрос сообщения доступен этому сотруднику.

    Возвращает None, если ограничение не нужно (admin/client).
    """
    if not is_manager_scoped(user):
        return None
    from sqlalchemy import and_, or_, exists
    from app.models.request import Request as RequestModel
    from app.models.message import Message
    return exists().where(
        and_(
            RequestModel.id == Message.request_id,
            or_(
                RequestModel.client_user_id.in_(managed_client_ids_subquery(user)),
                RequestModel.client_user_id == user.id,
                RequestModel.created_by_id == user.id,
                RequestModel.executor_id == user.id,
            ),
        )
    )


def clients_for_user_query(user: User):
    """Запрос активных клиентов, видимых пользователю.

    Сотрудник видит только своих закреплённых клиентов, администратор — всех.
    """
    query = select(User).where(User.role == UserRole.client, User.is_active == True)
    if is_manager_scoped(user):
        query = query.where(User.manager_id == user.id)
    return query.order_by(User.full_name)


async def staff_can_access_request(db: AsyncSession, user: User, req) -> bool:
    """Может ли сотрудник открыть запрос (свой клиент / свой запрос / его задача)."""
    if not is_manager_scoped(user):
        return True
    if req.created_by_id == user.id or req.executor_id == user.id or req.client_user_id == user.id:
        return True
    return await staff_can_access_client(db, user, req.client_user_id)


async def staff_can_access_client(db: AsyncSession, user: User, client_user_id: Optional[int]) -> bool:
    """Может ли сотрудник работать с указанным клиентом (закреплён ли за ним)."""
    if not is_manager_scoped(user):
        return True
    if not client_user_id:
        return False
    result = await db.execute(
        select(User.id).where(User.id == client_user_id, User.manager_id == user.id)
    )
    return result.scalar_one_or_none() is not None
