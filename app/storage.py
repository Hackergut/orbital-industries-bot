"""
Cloud storage for screenshots
"""
import os
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)


def upload_screenshot(file_path, name):
    """Upload screenshot to Cloudinary"""
    try:
        result = cloudinary.uploader.upload(
            file_path,
            public_id=name,
            folder="orbital/screenshots",
            resource_type="auto"
        )
        return result['secure_url']
    except Exception as e:
        print(f"Upload failed: {e}")
        return None


def upload_screenshot_bytes(file_bytes, name):
    """Upload screenshot from bytes"""
    try:
        result = cloudinary.uploader.upload(
            file_bytes,
            public_id=name,
            folder="orbital/screenshots",
            resource_type="auto"
        )
        return result['secure_url']
    except Exception as e:
        print(f"Upload failed: {e}")
        return None
