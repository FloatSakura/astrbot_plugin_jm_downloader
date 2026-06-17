# region 工具函数
"""JM 图片处理工具 — webp→jpg 转换 + PDF 合成 + ZIP 加密打包"""

import os
import asyncio
import io
import zipfile
import tempfile
import shutil
from pathlib import Path

from astrbot.api import logger

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

# PDF 预处理：长边超过此值则等比缩放
_PDF_MAX_LONG_EDGE = 2000
# JPEG 质量
_JPEG_QUALITY = 100


def _webp_to_jpg_bytes(webp_path: Path) -> bytes | None:
    """将 webp 图片转为 JPEG 字节流（quality=100），保留原尺寸。

    Returns:
        bytes 或 None（转换失败时）
    """
    try:
        img = Image.open(str(webp_path))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
        return buf.getvalue()
    except Exception as exc:
        logger.warning(f"webp→jpg 转换失败: {webp_path}: {exc}")
        return None


def _resize_for_pdf(img_path: Path) -> tuple[io.BytesIO, int, int]:
    """预处理图片用于 PDF：若长边 > 2000px 则等比缩放，输出为 JPEG (quality=100)。

    Returns:
        (BytesIO, width, height)
    """
    img = Image.open(str(img_path))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    w, h = img.size
    long_edge = max(w, h)
    if long_edge > _PDF_MAX_LONG_EDGE:
        scale = _PDF_MAX_LONG_EDGE / long_edge
        w = int(w * scale)
        h = int(h * scale)
        img = img.resize((w, h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    buf.seek(0)
    return buf, w, h


def images_to_pdf(image_paths: list[Path], pdf_path: str) -> bool:
    """将图片列表合并为一个 PDF，内部对图片做缩放+JPEG 压缩以减小体积。

    Args:
        image_paths: 图片 Path 列表（按顺序排列）
        pdf_path: 输出的 PDF 文件路径

    Returns:
        bool: 是否成功
    """
    if not image_paths:
        logger.warning("images_to_pdf: 图片列表为空")
        return False

    valid_paths = [p for p in image_paths if p.exists()]
    if not valid_paths:
        logger.warning("images_to_pdf: 所有图片文件均不存在")
        return False

    try:
        # 预处理第一张图片获取尺寸
        first_buf, first_w, first_h = _resize_for_pdf(valid_paths[0])
        c = canvas.Canvas(pdf_path, pagesize=(first_w, first_h))

        # 画第一张
        first_reader = ImageReader(first_buf)
        c.drawImage(first_reader, 0, 0, width=first_w, height=first_h)
        c.showPage()

        for i, img_path in enumerate(valid_paths[1:], start=1):
            try:
                buf, w, h = _resize_for_pdf(img_path)
                c.setPageSize((w, h))
                reader = ImageReader(buf)
                c.drawImage(reader, 0, 0, width=w, height=h)

                if i < len(valid_paths) - 1:
                    c.showPage()

            except Exception as exc:
                logger.warning(f"images_to_pdf: 跳过损坏图片 {img_path}: {exc}")
                continue

        c.save()
        logger.info(
            f"✅ PDF 生成成功: {pdf_path} ({len(valid_paths)} 页, "
            f"{os.path.getsize(pdf_path) / 1024 / 1024:.1f} MB)"
        )
        return True

    except Exception as exc:
        logger.error(f"❌ PDF 生成失败: {exc}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def images_to_pdf_async(image_paths: list[Path], pdf_path: str) -> bool:
    """异步包装 images_to_pdf"""
    return await asyncio.to_thread(images_to_pdf, image_paths, pdf_path)


def images_to_zip(
    image_paths: list[Path],
    zip_path: str,
    password: str = "",
) -> bool:
    """将图片列表打包为 ZIP 文件（webp 自动转 jpg 再打包，可选加密）。

    Args:
        image_paths: 图片 Path 列表（按顺序排列）
        zip_path: 输出的 ZIP 文件路径
        password: 加密密码，留空则不加密

    Returns:
        bool: 是否成功
    """
    import pyminizip

    if not image_paths:
        logger.warning("images_to_zip: 图片列表为空")
        return False

    valid_paths = [p for p in image_paths if p.exists()]
    if not valid_paths:
        logger.warning("images_to_zip: 所有图片文件均不存在")
        return False

    # 使用 ASCII 临时目录，避免 pyminizip 在 Windows 上无法处理中文路径
    temp_dir = None
    try:
        zip_parent = Path(zip_path).parent
        zip_parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="jm_zip_", dir=str(zip_parent)))

        # 把所有图片转换为 jpg 字节流，放到临时目录
        converted_paths: list[str] = []

        for i, img_path in enumerate(valid_paths):
            jpg_name = f"{i+1:04d}.jpg"
            jpg_file = temp_dir / jpg_name

            suffix = img_path.suffix.lower()
            if suffix == ".webp":
                jpg_bytes = _webp_to_jpg_bytes(img_path)
                if jpg_bytes is None:
                    # 转换失败，直接复制原文件
                    jpg_file.write_bytes(img_path.read_bytes())
                else:
                    jpg_file.write_bytes(jpg_bytes)
            else:
                # 非 webp 则直接转换（统一为 jpg，quality=100）
                try:
                    img = Image.open(str(img_path))
                    if img.mode in ("RGBA", "P", "LA"):
                        img = img.convert("RGB")
                    elif img.mode != "RGB":
                        img = img.convert("RGB")
                    img.save(str(jpg_file), format="JPEG", quality=_JPEG_QUALITY)
                except Exception:
                    jpg_file.write_bytes(img_path.read_bytes())

            converted_paths.append(str(jpg_file))

        # 先生成到纯 ASCII 临时路径，再移动到目标位置
        temp_zip = temp_dir / "output.zip"
        pyminizip.compress_multiple(
            converted_paths,
            [],
            str(temp_zip),
            password,
            5,  # compression level
        )

        # 移动到最终目标（同文件系统内为原子重命名）
        shutil.move(str(temp_zip), zip_path)

        logger.info(
            f"✅ ZIP 生成成功: {zip_path} ({len(converted_paths)} 文件, "
            f"{os.path.getsize(zip_path) / 1024 / 1024:.1f} MB, "
            f"{'加密' if password else '无加密'})"
        )
        return True

    except Exception as exc:
        logger.error(f"❌ ZIP 生成失败: {exc}")
        import traceback
        logger.error(traceback.format_exc())
        return False
    finally:
        # 确保清理临时目录
        if temp_dir is not None and temp_dir.exists():
            try:
                shutil.rmtree(str(temp_dir))
            except Exception:
                pass


async def images_to_zip_async(
    image_paths: list[Path],
    zip_path: str,
    password: str = "",
) -> bool:
    """异步包装 images_to_zip"""
    return await asyncio.to_thread(images_to_zip, image_paths, zip_path, password)


# endregion