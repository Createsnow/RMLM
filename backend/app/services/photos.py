"""现场照片：压缩到约 300KB、加时间与人员水印、永久保存，不能删除只能补传。"""
import hashlib
import io
import uuid

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..errors import BizError
from ..models import Photo, User

MAX_UPLOAD = 15 * 1024 * 1024


# 水印里有人名，必须用中文字体。没配置 RMLM_WATERMARK_FONT 时按顺序找常见系统字体（Linux / Windows / Mac）
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
)


def _font(size: int):
    paths = ([settings.watermark_font] if settings.watermark_font else []) + list(FONT_CANDIDATES)
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()  # 不含中文：人名会显示成方块


def save_photo(db: Session, user: User, raw: bytes) -> Photo:
    if len(raw) > MAX_UPLOAD:
        raise BizError("照片不能超过 15MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise BizError("文件不是有效的图片")
    img.thumbnail((1600, 1600))
    draw = ImageDraw.Draw(img)
    text = f"{clock.now():%Y-%m-%d %H:%M} {user.name}"
    size = max(16, img.width // 40)
    font = _font(size)
    x, y = 12, img.height - size - 16
    bbox = draw.textbbox((x, y), text, font=font)
    draw.rectangle((bbox[0] - 6, bbox[1] - 4, bbox[2] + 6, bbox[3] + 4), fill=(0, 0, 0))
    draw.text((x, y), text, fill=(255, 255, 255), font=font)
    data = b""
    for quality in (85, 75, 65, 55, 45, 40):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= settings.photo_max_kb * 1024:
            break
    rel = f"{clock.now():%Y/%m}/{uuid.uuid4().hex}.jpg"
    path = settings.photo_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    p = Photo(path=rel, uploader_id=user.id, size=len(data), sha1=hashlib.sha1(data).hexdigest())
    db.add(p)
    db.flush()
    return p
