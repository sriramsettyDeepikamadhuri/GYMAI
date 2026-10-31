import secrets
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app import models, schemas
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.services.notify import email_recipients, norm, publish_event
router=APIRouter(prefix='/api/notifications',tags=['notifications'])
def require_admin(x_admin_key:str|None=Header(default=None))->None:
    key=settings.admin_api_key
    if not key or not x_admin_key or not secrets.compare_digest(x_admin_key,key):raise HTTPException(status_code=403,detail='Admin key required.')
def _pref(db:Session,user_id:str):return db.get(models.UserPreference,user_id)
@router.get('/preferences',response_model=schemas.NotificationPreferenceOut|None)
def get_preferences(user:models.User=Depends(get_current_user),db:Session=Depends(get_db)):return _pref(db,user.id)
@router.put('/preferences',response_model=schemas.NotificationPreferenceOut)
def save_preferences(payload:schemas.NotificationPreferenceIn,user:models.User=Depends(get_current_user),db:Session=Depends(get_db)):
    pref=_pref(db,user.id) or models.UserPreference(user_id=user.id);pref.notification_email=payload.notification_email.strip().lower();pref.country=norm(payload.country);pref.state=norm(payload.state);pref.area=norm(payload.area);pref.email_enabled=payload.email_enabled;db.add(pref);db.commit();db.refresh(pref);return pref
@router.get('',response_model=schemas.NotificationFeedOut)
def list_notifications(unread_only:bool=False,limit:int=30,user:models.User=Depends(get_current_user),db:Session=Depends(get_db)):
    pref=_pref(db,user.id)
    if not pref:return schemas.NotificationFeedOut(unread_count=0,items=[])
    read_ids=select(models.NotificationRead.notification_id).where(models.NotificationRead.user_id==user.id)
    scope=(func.lower(models.Notification.country)==pref.country.lower(),func.lower(models.Notification.state)==pref.state.lower(),func.lower(models.Notification.area)==pref.area.lower())
    unread_count=db.scalar(select(func.count()).select_from(models.Notification).where(*scope,models.Notification.notification_id.not_in(read_ids))) or 0
    q=select(models.Notification).where(*scope).order_by(models.Notification.created_at.desc()).limit(min(limit,100))
    if unread_only:q=q.where(models.Notification.notification_id.not_in(read_ids))
    rows=db.execute(q).scalars().all();read_set=set(db.execute(read_ids).scalars().all())
    return schemas.NotificationFeedOut(unread_count=unread_count,items=[schemas.NotificationOut.model_validate(r).model_copy(update={'is_read':r.notification_id in read_set}) for r in rows])
@router.get('/marathons',response_model=schemas.MarathonAvailabilityOut)
def marathon_availability(user:models.User=Depends(get_current_user),db:Session=Depends(get_db)):
    pref=_pref(db,user.id)
    if not pref:return schemas.MarathonAvailabilityOut(available=False,message='Set your state and area to check marathon availability here.',items=[])
    rows=db.execute(select(models.Notification).where(func.lower(models.Notification.country)==pref.country.lower(),func.lower(models.Notification.state)==pref.state.lower(),func.lower(models.Notification.area)==pref.area.lower()).order_by(models.Notification.created_at.desc()).limit(20)).scalars().all()
    if not rows:return schemas.MarathonAvailabilityOut(available=False,message=f'No marathon available in {pref.area}, {pref.state} right now.',items=[])
    return schemas.MarathonAvailabilityOut(available=True,message=f'Marathon available in {pref.area}, {pref.state}.',items=[schemas.NotificationOut.model_validate(r) for r in rows])
@router.post('/read')
def mark_read(payload:schemas.MarkReadIn,user:models.User=Depends(get_current_user),db:Session=Depends(get_db)):
    pref=_pref(db,user.id)
    if not pref:return {'marked':0}
    q=select(models.Notification.notification_id).where(func.lower(models.Notification.country)==pref.country.lower(),func.lower(models.Notification.state)==pref.state.lower(),func.lower(models.Notification.area)==pref.area.lower())
    if payload.notification_ids is not None:q=q.where(models.Notification.notification_id.in_(payload.notification_ids))
    ids=set(db.execute(q).scalars().all());already=set(db.execute(select(models.NotificationRead.notification_id).where(models.NotificationRead.user_id==user.id,models.NotificationRead.notification_id.in_(ids or ['']))).scalars().all())
    for nid in ids-already:db.add(models.NotificationRead(user_id=user.id,notification_id=nid))
    db.commit();return {'marked':len(ids-already)}
@router.post('/events',status_code=201,dependencies=[Depends(require_admin)])
def create_event(payload:schemas.EventCreateIn,db:Session=Depends(get_db)):
    note,recipients=publish_event(db,title=payload.title,message_text=payload.message_text,country=payload.country,state=payload.state,area=payload.area,target_url=payload.target_url)
    failed=email_recipients(note.notification_id,recipients)
    if recipients and failed:raise HTTPException(status_code=503,detail=f'Marathon saved, but {len(failed)} event email(s) could not be sent. Please try again later.')
    return {'notification_id':note.notification_id,'emails_sent':len(recipients)}
