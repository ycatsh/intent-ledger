import io

from flask import send_file


def send_export(export: tuple[str, bytes]):
    filename, content = export
    return send_file(io.BytesIO(content), as_attachment=True, download_name=filename)
