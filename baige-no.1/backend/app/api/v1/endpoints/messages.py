from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_

from app.api.deps import get_db, get_current_user
from app.models.message import Message, MessageStatus
from app.models.user import User
from app.schemas.message import Message as MessageSchema, MessageCreate, MessageUpdate
from app.schemas.common import ResponseModel, PaginatedResponse

router = APIRouter()


@router.get("", response_model=ResponseModel[PaginatedResponse[MessageSchema]])
async def list_messages(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status: str = Query(None),
    msg_type: str = Query(None),
) -> Any:
    query = select(Message).where(
        or_(Message.receiver_id == current_user.id, Message.receiver_id == None)
    ).order_by(Message.created_at.desc())
    
    count_query = select(func.count(Message.id)).where(
        or_(Message.receiver_id == current_user.id, Message.receiver_id == None)
    )
    
    filters = []
    if status:
        filters.append(Message.status == status)
    if msg_type:
        filters.append(Message.msg_type == msg_type)
    
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    messages = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[MessageSchema.model_validate(m) for m in messages],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.get("/unread-count", response_model=ResponseModel[int])
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(
        select(func.count(Message.id)).where(
            and_(
                Message.status == MessageStatus.UNREAD.value,
                or_(Message.receiver_id == current_user.id, Message.receiver_id == None)
            )
        )
    )
    count = result.scalar()
    return ResponseModel(data=count)


@router.post("", response_model=ResponseModel[MessageSchema])
async def create_message(
    msg_in: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    db_msg = Message(
        title=msg_in.title,
        content=msg_in.content,
        msg_type=msg_in.msg_type,
        sender_id=current_user.id,
        sender_name=current_user.username,
        receiver_id=msg_in.receiver_id,
    )
    db.add(db_msg)
    await db.commit()
    await db.refresh(db_msg)
    return ResponseModel(data=MessageSchema.model_validate(db_msg))


@router.put("/{msg_id}/read", response_model=ResponseModel[MessageSchema])
async def mark_read(
    msg_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(select(Message).where(Message.id == msg_id))
    db_msg = result.scalar_one_or_none()
    if not db_msg:
        raise HTTPException(status_code=404, detail="消息不存在")
    
    db_msg.status = MessageStatus.READ.value
    await db.commit()
    await db.refresh(db_msg)
    return ResponseModel(data=MessageSchema.model_validate(db_msg))
