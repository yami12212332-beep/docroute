"""
Builds the two Docling converters the router switches between per page:

  - native_converter: do_ocr=False, trusts the existing text layer as-is.
    Fast -- no OCR model inference at all.
  - ocr_converter: RapidOcrOptions(mode=OcrMode.FULL_PAGE), discards the
    text layer and re-OCRs from the rendered page image.

Both keep picture/table extraction on, since the router's job is only to
pick the right TEXT strategy -- images and tables get extracted either way.

Built once each and reused across every file/page (model loading is the
expensive part -- don't rebuild these per page).
"""

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    OcrMode,
    PdfPipelineOptions,
    RapidOcrOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

from docroute.config import IMAGE_RESOLUTION_SCALE, OCR_LANG


def _base_pipeline_options(do_ocr: bool, ocr_mode: OcrMode | None = None) -> PdfPipelineOptions:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr
    pipeline_options.do_table_structure = True

    # RapidOcrOptions is a BUILT-IN Docling option, not a third-party
    # plugin -- keep this False unless you actually wire up an external
    # plugin package.
    pipeline_options.allow_external_plugins = False

    if do_ocr:
        pipeline_options.ocr_options = RapidOcrOptions(mode=ocr_mode, lang=OCR_LANG)
        assert pipeline_options.ocr_options.mode == ocr_mode, (
            "OCR mode did not resolve as expected -- check installed Docling "
            "version (`uv pip show docling`) against issue #4082."
        )

    pipeline_options.table_structure_options.mode = TableFormerMode.FAST

    # Keep images so the image extraction module has something to pull.
    pipeline_options.images_scale = IMAGE_RESOLUTION_SCALE
    pipeline_options.generate_page_images = True
    pipeline_options.generate_picture_images = True

    pipeline_options.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice.AUTO,
        num_threads=8,
    )
    return pipeline_options


def _converter_from(pipeline_options: PdfPipelineOptions) -> DocumentConverter:
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )


def build_native_converter() -> DocumentConverter:
    """No OCR -- trusts the existing text layer. Use on pages the quality
    scorer marked as clean."""
    return _converter_from(_base_pipeline_options(do_ocr=False))


def build_ocr_converter() -> DocumentConverter:
    """Forced full-page OCR -- discards the existing text layer entirely.
    Use on pages the quality scorer flagged as corrupted/scanned."""
    return _converter_from(_base_pipeline_options(do_ocr=True, ocr_mode=OcrMode.FULL_PAGE))
