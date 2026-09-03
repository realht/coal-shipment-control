import zipfile

from django.conf import settings
from django import forms

from .models import ShipmentDocument

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "xlsx", "docx"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_MAGIC = [
    (b"\x25\x50\x44\x46", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"PK\x03\x04", None),
]


def _detect_mime(file_obj: object, header: bytes) -> str:
    for magic, mime in _MAGIC:
        if header.startswith(magic):
            if mime is None:
                return _detect_office_zip(file_obj)
            return mime
    return "application/octet-stream"


def _detect_office_zip(file_obj) -> str:
    pos = file_obj.tell()
    try:
        with zipfile.ZipFile(file_obj) as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" not in names:
                return "application/zip"
            if "xl/workbook.xml" in names:
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if "word/document.xml" in names:
                return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    except zipfile.BadZipFile:
        return "application/zip"
    finally:
        file_obj.seek(pos)
    return "application/zip"


class EditDocumentForm(forms.Form):
    document_type = forms.ChoiceField(
        choices=ShipmentDocument.DOCUMENT_TYPE_CHOICES,
        label="Тип документа",
    )
    file = forms.FileField(label="Заменить файл", required=False)

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f:
            return f
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"Недопустимый тип файла. Разрешены: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )
        max_size_mb = settings.MAX_UPLOAD_SIZE_MB
        if f.size > max_size_mb * 1024 * 1024:
            raise forms.ValidationError(f"Файл превышает допустимый размер {max_size_mb} МБ.")
        header = f.read(261)
        f.seek(0)
        detected = _detect_mime(f, header)
        if detected not in ALLOWED_MIME_TYPES:
            raise forms.ValidationError(
                "Тип содержимого файла не соответствует разрешённым форматам."
            )
        f.detected_mime_type = detected
        return f


class UploadDocumentForm(forms.Form):
    document_type = forms.ChoiceField(
        choices=ShipmentDocument.DOCUMENT_TYPE_CHOICES,
        label="Тип документа",
    )
    file = forms.FileField(label="Файл")

    def clean_file(self):
        f = self.cleaned_data["file"]
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"Недопустимый тип файла. Разрешены: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )
        max_size_mb = settings.MAX_UPLOAD_SIZE_MB
        max_size_bytes = max_size_mb * 1024 * 1024
        if f.size > max_size_bytes:
            raise forms.ValidationError(f"Файл превышает допустимый размер {max_size_mb} МБ.")
        header = f.read(261)
        f.seek(0)
        detected = _detect_mime(f, header)
        if detected not in ALLOWED_MIME_TYPES:
            raise forms.ValidationError(
                "Тип содержимого файла не соответствует разрешённым форматам."
            )
        f.detected_mime_type = detected
        return f
