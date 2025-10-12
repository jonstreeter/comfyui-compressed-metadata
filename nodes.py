import os
import json
import zlib
import base64
import piexif
import numpy as np
from PIL import Image
from pathlib import Path
import folder_paths
import traceback
import torch

class SaveImageCompressed:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "quality": ("INT", {"default": 95, "min": 1, "max": 100}),
                "format": (["JPEG", "WEBP"], {"default": "JPEG"}),
            },
            "hidden": {
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }
    
    RETURN_TYPES = ()
    OUTPUT_NODE = True
    FUNCTION = "save_images"
    CATEGORY = "image"

    def save_images(self, images, filename_prefix="ComfyUI", quality=95, format="JPEG", extra_pnginfo=None):
        # Extract UI workflow from extra_pnginfo (injected by JS)
        workflow_dict = {}
        try:
            if isinstance(extra_pnginfo, dict):
                workflow_dict = extra_pnginfo.get('workflow', {})
                if workflow_dict:
                    print(f"[Compressed Metadata] Extracted UI workflow from extra_pnginfo (with {len(workflow_dict.get('nodes', []))} nodes).")
                else:
                    print("[Compressed Metadata] No 'workflow' key in extra_pnginfo— using empty workflow.")
            else:
                print("[Compressed Metadata] extra_pnginfo not provided— using empty workflow.")
        except Exception as e:
            print(f"[Compressed Metadata] Error extracting UI workflow from extra_pnginfo: {e}")
            workflow_dict = {}

        try:
            workflow_str = json.dumps(workflow_dict)
        except Exception as e:
            print(f"[Compressed Metadata] Error serializing workflow: {e}")
            workflow_str = "{}"

        # Fallback message for users without the extension (dummy UI workflow)
        fallback = {
            "last_node_id": 0,
            "last_link_id": 0,
            "nodes": [],
            "links": [],
            "groups": [],
            "config": {},
            "extra": {"note": "Install compressed-metadata extension to load the actual workflow."}
        }
        fallback_str = json.dumps(fallback)

        # Output folder
        output_dir = folder_paths.get_output_directory()
        full_output_folder = Path(output_dir)
        full_output_folder.mkdir(parents=True, exist_ok=True)

        results = []
        counter = 1

        for image in images:
            # Handle tensor conversion properly (matching ComfyUI's SaveImage)
            if isinstance(image, torch.Tensor):
                i_array = 255. * image.cpu().numpy()
                i_array = np.clip(i_array, 0, 255).astype(np.uint8)
                
                if len(i_array.shape) == 4:  # Batch
                    i_array = i_array[0]
                if len(i_array.shape) == 3:
                    if i_array.shape[2] == 4:  # RGBA -> RGB
                        i_array = i_array[:, :, :3]
                    elif i_array.shape[2] == 1:  # Grayscale -> RGB
                        i_array = np.repeat(i_array, 3, axis=2)
                pil_image = Image.fromarray(i_array)
                if pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')
            elif isinstance(image, Image.Image):
                pil_image = image
                if pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')
            else:
                pil_image = Image.fromarray(np.clip(image * 255, 0, 255).astype(np.uint8))
                if pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')

            # Generate filename based on desired format
            ext = ".jpg" if format == "JPEG" else ".webp"
            filename = f"{filename_prefix}_{counter:05}{ext}"
            filepath = full_output_folder / filename

            try:
                # Compress the UI workflow
                compressed = zlib.compress(workflow_str.encode('utf-8'))
                encoded = base64.b64encode(compressed).decode('utf-8')
                compressed_flag = f"COMPRESSED:{encoded}"
                pil_image.info['prompt'] = fallback_str
            except Exception as e:
                print(f"[Compressed Metadata] Error compressing workflow: {e}")
                compressed_flag = "COMPRESSED:ERROR"

            # Build EXIF with UserComment
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
            prefix = b'ASCII\x00\x00\x00'
            exif_dict["Exif"][piexif.ExifIFD.UserComment] = prefix + compressed_flag.encode('ascii', errors='ignore') + b'\x00'
            exif_bytes = piexif.dump(exif_dict)

            # Decide per-file output format; fallback to WEBP if JPEG EXIF payload is too large
            JPEG_EXIF_SOFT_LIMIT = 64000
            chosen_format = "JPEG" if format == "JPEG" else "WEBP"
            chosen_ext = ".jpg" if format == "JPEG" else ".webp"
            try:
                uc_bytes = exif_dict["Exif"][piexif.ExifIFD.UserComment]
                uc_len = len(uc_bytes) if isinstance(uc_bytes, (bytes, bytearray)) else len(bytes(uc_bytes))
            except Exception:
                uc_len = 0
            if format == "JPEG" and uc_len > JPEG_EXIF_SOFT_LIMIT:
                print(f"[Compressed Metadata] EXIF payload {uc_len} bytes too large for JPEG → switching to WEBP for this file")
                chosen_format = "WEBP"
                chosen_ext = ".webp"
                base = f"{filename_prefix}_{counter:05}"
                filename = base + chosen_ext
                filepath = full_output_folder / filename

            try:
                pil_image.save(str(filepath), chosen_format, quality=quality, exif=exif_bytes)
                results.append({"filename": filename, "subfolder": "", "type": "output"})
                print(f"[Compressed Metadata] Saved: {filename} ({chosen_format}) with compressed EXIF metadata")
                counter += 1
            except Exception as e:
                print(f"[Compressed Metadata] Error saving image {filename}: {e}\n{traceback.format_exc()}")

        if not results:
            raise ValueError("No images were successfully saved.")

        return {"ui": {"images": results}}


class ConvertToCompressed:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "folder_path": ("STRING", {"default": folder_paths.get_output_directory()}),
                "recurse": ("BOOLEAN", {"default": True}),
                "delete_originals": ("BOOLEAN", {"default": False}),
                "quality": ("INT", {"default": 95, "min": 1, "max": 100}),
                "output_format": (["JPEG", "WEBP"], {"default": "JPEG"}),
            }
        }
    
    RETURN_TYPES = ("STRING",)
    FUNCTION = "convert"
    CATEGORY = "utils"

    def convert(self, folder_path, recurse=True, delete_originals=False, quality=95, output_format="JPEG"):
        start_path = Path(folder_path)
        if not start_path.exists():
            raise ValueError(f"Path does not exist: {folder_path}")

        converted = 0
        errors = 0

        try:
            # Collect candidate PNGs (as source of embedded workflow); adjust as needed
            png_files = []
            if start_path.is_file():
                png_files = [start_path] if start_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else []
            else:
                if recurse:
                    for root, _, files in os.walk(start_path):
                        for f in files:
                            if f.lower().endswith(".png"):
                                png_files.append(Path(root) / f)
                else:
                    png_files = [start_path / f for f in os.listdir(start_path) if f.lower().endswith(".png")]

            for png_path in png_files:
                try:
                    with Image.open(png_path) as img:
                        # Extract existing Comfy workflow JSON from PNG text (if any)
                        workflow_json = None
                        try:
                            if hasattr(img, "text") and isinstance(img.text, dict):
                                workflow_json = img.text.get("workflow")
                        except Exception:
                            workflow_json = None

                        if workflow_json:
                            try:
                                workflow_obj = json.loads(workflow_json)
                                compressed = zlib.compress(json.dumps(workflow_obj).encode('utf-8'))
                                encoded = base64.b64encode(compressed).decode('utf-8')
                                compressed_flag = f"COMPRESSED:{encoded}"
                            except Exception as e:
                                print(f"[Compressed Metadata] {png_path.name}: error compressing embedded workflow: {e}")
                                compressed_flag = "COMPRESSED:ERROR"
                        else:
                            compressed_flag = "COMPRESSED:{}"

                        # Minimal human-friendly fallback in 'prompt'
                        fallback = {
                            "last_node_id": 0,
                            "last_link_id": 0,
                            "nodes": [],
                            "links": [],
                            "groups": [],
                            "config": {},
                            "extra": {"note": "Install compressed-metadata extension to load the actual workflow."}
                        }
                        img.info['prompt'] = json.dumps(fallback)

                        # Build EXIF with UserComment
                        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
                        prefix = b'ASCII\x00\x00\x00'
                        exif_dict["Exif"][piexif.ExifIFD.UserComment] = prefix + compressed_flag.encode('ascii', errors='ignore') + b'\x00'
                        exif_bytes = piexif.dump(exif_dict)

                        # Decide per-file output format; fallback to WEBP if JPEG EXIF payload is too large
                        JPEG_EXIF_SOFT_LIMIT = 64000
                        chosen_format = "JPEG" if output_format == "JPEG" else "WEBP"
                        chosen_ext = ".jpg" if output_format == "JPEG" else ".webp"
                        try:
                            uc_bytes = exif_dict["Exif"][piexif.ExifIFD.UserComment]
                            uc_len = len(uc_bytes) if isinstance(uc_bytes, (bytes, bytearray)) else len(bytes(uc_bytes))
                        except Exception:
                            uc_len = 0
                        if output_format == "JPEG" and uc_len > JPEG_EXIF_SOFT_LIMIT:
                            print(f"[Compressed Metadata] {png_path.name}: EXIF payload {uc_len} bytes too large for JPEG → switching to WEBP")
                            chosen_format = "WEBP"
                            chosen_ext = ".webp"

                        # Convert to RGB if necessary
                        if img.mode in ('RGBA', 'LA', 'P', 'CMYK'):
                            if img.mode == 'CMYK':
                                img = img.convert('RGB')
                            else:
                                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                                if img.mode in ('RGBA', 'LA'):
                                    rgb_img.paste(img, mask=img.split()[-1])
                                else:
                                    rgb_img.paste(img)
                                img = rgb_img
                        elif img.mode != 'RGB':
                            img = img.convert('RGB')
                        
                        # Output path (respect chosen extension)
                        output_path = png_path.with_suffix(chosen_ext)
                        
                        try:
                            img.save(str(output_path), chosen_format, quality=quality, exif=exif_bytes)
                            print(f"[Compressed Metadata] Saved: {output_path} ({chosen_format}) with compressed EXIF metadata")
                            if delete_originals:
                                png_path.unlink()
                                print(f"[Compressed Metadata] Deleted original: {png_path}")
                            converted += 1
                        except Exception as save_e:
                            print(f"[Compressed Metadata] Error saving converted image {output_path}: {save_e}")
                            errors += 1
                            
                except Exception as e:
                    print(f"[Compressed Metadata] Error converting {png_path}: {e}\n{traceback.format_exc()}")
                    errors += 1
        except Exception as e:
            raise ValueError(f"Error during conversion: {e}")

        result_msg = f"Converted {converted} images to {output_format}."
        if errors > 0:
            result_msg += f" {errors} errors encountered (check console)."
        return (result_msg,)


class LoadCompressedWorkflow:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image_path": ("STRING", {"default": ""}),
            }
        }
    
    RETURN_TYPES = ("STRING",)
    FUNCTION = "load_workflow"
    CATEGORY = "utils"

    def load_workflow(self, image_path):
        try:
            image_path = Path(image_path)
            if not image_path.exists():
                raise ValueError(f"Image not found: {image_path}")
            with Image.open(image_path) as img:
                exif_bytes = img.info.get('exif', None)
                if not exif_bytes:
                    raise ValueError("No EXIF found in image.")
                try:
                    exif_dict = piexif.load(exif_bytes)
                except Exception as e:
                    raise ValueError(f"Failed to parse EXIF: {e}")
                uc = exif_dict.get("Exif", {}).get(piexif.ExifIFD.UserComment, None)
                if not uc:
                    raise ValueError("No UserComment found in EXIF.")
                if isinstance(uc, bytes) and len(uc) >= 8:
                    uc_str = uc[8:].rstrip(b'\x00').decode('ascii', errors='ignore')
                else:
                    uc_str = str(uc)
                if uc_str.startswith("COMPRESSED:"):
                    payload = uc_str[len("COMPRESSED:"):]
                    try:
                        decompressed = zlib.decompress(base64.b64decode(payload))
                        workflow_json = decompressed.decode('utf-8')
                        return (workflow_json,)
                    except Exception as e:
                        raise ValueError(f"Failed to decompress workflow: {e}")
                else:
                    prompt_text = img.info.get('prompt', None)
                    if prompt_text:
                        return (prompt_text,)
                    raise ValueError("No compressed workflow found.")
        except Exception as e:
            raise ValueError(f"Error loading workflow: {e}")


class LoadWorkflowJSON:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "json_path": ("STRING", {"default": ""}),
            }
        }
    RETURN_TYPES = ("STRING",)
    FUNCTION = "load_json"
    CATEGORY = "utils"

    def load_json(self, json_path):
        try:
            p = Path(json_path)
            if not p.exists():
                raise ValueError(f"File not found: {json_path}")
            text = p.read_text(encoding='utf-8')
            try:
                json.loads(text)
            except Exception as e:
                raise ValueError(f"Invalid JSON: {e}")
            return (text,)
        except Exception as e:
            raise ValueError(f"Error reading JSON file: {e}")


class ExtractWorkflowsToJSON:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "input_folder": ("STRING", {"default": folder_paths.get_output_directory()}),
                "output_folder": ("STRING", {"default": folder_paths.get_output_directory()}),
                "recurse": ("BOOLEAN", {"default": True}),
                "use_image_name": ("BOOLEAN", {"default": True}),
                "append_suffix": ("STRING", {"default": "_workflow.json"}),
            }
        }
    
        # No return images; a status string instead
    RETURN_TYPES = ("STRING",)
    FUNCTION = "extract"
    CATEGORY = "utils"

    def extract(self, input_folder, output_folder, recurse=True, use_image_name=True, append_suffix="_workflow.json"):
        input_path = Path(input_folder)
        output_path = Path(output_folder)
        output_path.mkdir(parents=True, exist_ok=True)

        if not input_path.exists():
            raise ValueError(f"Input path does not exist: {input_folder}")

        processed = 0
        extracted = 0
        errors = 0

        try:
            image_files = []
            if input_path.is_file():
                image_files = [input_path] if input_path.suffix.lower() in (".jpg", ".jpeg", ".webp") else []
            else:
                if recurse:
                    for root, _, files in os.walk(input_path):
                        for f in files:
                            if f.lower().endswith((".jpg", ".jpeg", ".webp")):
                                image_files.append(Path(root) / f)
                else:
                    image_files = [input_path / f for f in os.listdir(input_path) if f.lower().endswith((".jpg", ".jpeg", ".webp"))]

            for image_path in image_files:
                processed += 1
                try:
                    with Image.open(image_path) as img:
                        exif_bytes = img.info.get('exif', None)
                        if not exif_bytes:
                            print(f"[Compressed Metadata] No EXIF found in {image_path}, skipping.")
                            continue
                        try:
                            exif_dict = piexif.load(exif_bytes)
                        except Exception as e:
                            print(f"[Compressed Metadata] Failed to parse EXIF for {image_path}: {e}")
                            continue
                        uc = exif_dict.get("Exif", {}).get(piexif.ExifIFD.UserComment, None)
                        if not uc:
                            print(f"[Compressed Metadata] No UserComment in {image_path}, skipping.")
                            continue

                        if isinstance(uc, bytes) and len(uc) >= 8:
                            uc_str = uc[8:].rstrip(b'\x00').decode('ascii', errors='ignore')
                        else:
                            uc_str = str(uc)

                        workflow_json = None
                        if uc_str.startswith("COMPRESSED:"):
                            payload = uc_str[len("COMPRESSED:"):]
                            try:
                                decompressed = zlib.decompress(base64.b64decode(payload))
                                workflow_json = decompressed.decode('utf-8')
                                extracted += 1
                            except Exception as e:
                                print(f"[Compressed Metadata] Decompression failed for {image_path}: {e}")
                                continue
                        else:
                            prompt_text = img.info.get('prompt', None)
                            if prompt_text:
                                workflow_json = prompt_text
                                extracted += 1

                        if workflow_json:
                            if use_image_name:
                                out_name = image_path.stem + append_suffix
                            else:
                                out_name = f"workflow_{processed:05}{append_suffix}"
                            out_file = output_path / out_name
                            out_file.write_text(workflow_json, encoding='utf-8')
                            print(f"[Compressed Metadata] Wrote {out_file}")
                        else:
                            print(f"[Compressed Metadata] No valid UI graph in {image_path}, skipping.")
                        
                except Exception as e:
                    print(f"[Compressed Metadata] Error extracting from {image_path}: {e}\n{traceback.format_exc()}")
                    errors += 1
        except Exception as e:
            raise ValueError(f"Error during extraction: {e}")

        result_msg = f"Processed {processed} images: {extracted} workflows extracted to JSON, {errors} errors."
        return (result_msg,)


# Register nodes
NODE_CLASS_MAPPINGS = {
    "SaveImageCompressed": SaveImageCompressed,
    "ConvertToCompressed": ConvertToCompressed,
    "LoadCompressedWorkflow": LoadCompressedWorkflow,
    "LoadWorkflowJSON": LoadWorkflowJSON,
    "ExtractWorkflowsToJSON": ExtractWorkflowsToJSON,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SaveImageCompressed": "🔒 Save Compressed Image",
    "ConvertToCompressed": "🔄 Convert to Compressed Image",
    "LoadCompressedWorkflow": "📂 Load Compressed Workflow",
    "LoadWorkflowJSON": "📁 Load Workflow JSON",
    "ExtractWorkflowsToJSON": "📤 Extract Workflows to JSON",
}
