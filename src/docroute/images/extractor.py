"""
Pulls PictureItem crops out of a converted Docling document.

Requires generate_picture_images=True on the pipeline options used to
produce the ConversionResult (already set in pipeline/converter.py) --
without it, PictureItem.get_image() returns None.
"""

from dataclasses import dataclass

from docling_core.types.doc import PictureItem
from PIL import Image


@dataclass
class ExtractedImage:
    ref: str          # element.self_ref -- unique id within the document
    page_number: int
    image: Image.Image


def extract_images(docling_result) -> list[ExtractedImage]:
    doc = docling_result.document
    images: list[ExtractedImage] = []

    for element, _level in doc.iterate_items():
        if not isinstance(element, PictureItem):
            continue
        img = element.get_image(doc)
        if img is None:
            continue  # generate_picture_images wasn't on, or extraction failed for this element
        page_no = element.prov[0].page_no if element.prov else 0
        images.append(ExtractedImage(ref=element.self_ref, page_number=page_no, image=img))

    return images
