from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.commercial_area import FavoriteArea, CommercialArea, User
from app.dependencies import get_current_user

router = APIRouter(prefix="/favorites", tags=["favorites"])

@router.get("")
def get_favorites(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    favorites = db.query(FavoriteArea).filter(FavoriteArea.user_id == current_user.id).all()
    results = []
    for fav in favorites:
        # Load the associated area to get the name
        if fav.area:
            results.append({
                "area_code": fav.area_code,
                "area_name": fav.area.area_name,
                "district_code": fav.area.district_code,
                "id": fav.id
            })
    return results

@router.post("/{area_code}")
def add_favorite(area_code: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    area = db.query(CommercialArea).filter(CommercialArea.area_code == area_code).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
        
    existing = db.query(FavoriteArea).filter(FavoriteArea.area_code == area_code, FavoriteArea.user_id == current_user.id).first()
    if existing:
        return {"message": "Already in favorites", "status": "exists"}
        
    new_fav = FavoriteArea(area_code=area_code, user_id=current_user.id)
    db.add(new_fav)
    db.commit()
    return {"message": "Added to favorites", "status": "added"}

@router.delete("/{area_code}")
def remove_favorite(area_code: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    existing = db.query(FavoriteArea).filter(FavoriteArea.area_code == area_code, FavoriteArea.user_id == current_user.id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Favorite not found")
        
    db.delete(existing)
    db.commit()
    return {"message": "Removed from favorites", "status": "removed"}
