from __future__ import annotations

import multiprocessing
import os
import queue
import threading
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, filedialog, messagebox
import tkinter as tk
from tkinter import ttk


RAW_SUFFIXES = {".nef", ".nrw", ".dng"}
HEIC_SUFFIXES = {".heic", ".heif"}
SUPPORTED_SOURCE_SUFFIXES = RAW_SUFFIXES | HEIC_SUFFIXES
CPU_COUNT = os.cpu_count() or 1


@dataclass(frozen=True)
class ConvertTask:
    source: Path
    destination: Path


@dataclass(frozen=True)
class ConvertResult:
    task: ConvertTask
    error: str | None = None
    debug: str = ""


def find_convertible_files(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(
        path
        for path in folder.glob(pattern)
        if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
    )


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 1

    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def parse_photo_datetime(value: object) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="ignore")
    else:
        text = str(value)
    text = text.strip().split("\x00", 1)[0]

    for date_format in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text[:19], date_format)
        except ValueError:
            continue
    return None


def read_raw_capture_datetime(source: Path) -> datetime | None:
    try:
        import exifread
    except ImportError as exc:
        raise RuntimeError(
            "缺少拍摄时间读取依赖。请先运行：pip install -r requirements.txt"
        ) from exc

    with source.open("rb") as source_file:
        tags = exifread.process_file(source_file, details=False)

    for tag_name in (
        "EXIF DateTimeOriginal",
        "EXIF DateTimeDigitized",
        "Image DateTime",
    ):
        captured_at = parse_photo_datetime(tags.get(tag_name))
        if captured_at is not None:
            return captured_at
    return None


def read_heic_capture_datetime(source: Path) -> datetime | None:
    try:
        from PIL import ExifTags, Image
        from pillow_heif import register_heif_opener
    except ImportError as exc:
        raise RuntimeError(
            "缺少 HEIC 元数据读取依赖。请先运行：pip install -r requirements.txt"
        ) from exc

    register_heif_opener()
    with Image.open(source) as image:
        exif = image.getexif()
        values = [exif.get(36867), exif.get(36868), exif.get(306)]
        try:
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        except (AttributeError, KeyError, TypeError):
            exif_ifd = {}
        values = [exif_ifd.get(36867), exif_ifd.get(36868), *values]

    for value in values:
        captured_at = parse_photo_datetime(value)
        if captured_at is not None:
            return captured_at
    return None


def datetime_name_for_source(source: Path) -> str:
    try:
        if source.suffix.lower() in RAW_SUFFIXES:
            captured_at = read_raw_capture_datetime(source)
        elif source.suffix.lower() in HEIC_SUFFIXES:
            captured_at = read_heic_capture_datetime(source)
        else:
            captured_at = None
    except Exception:
        captured_at = None

    if captured_at is None:
        captured_at = datetime.fromtimestamp(source.stat().st_ctime)
    return captured_at.strftime("%Y%m%d%H%M%S")


def convert_raw_to_jpg(source: Path, destination: Path, quality: int, use_camera_wb: bool) -> None:
    try:
        import rawpy
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "缺少转换依赖。请先运行：pip install -r requirements.txt"
        ) from exc

    destination.parent.mkdir(parents=True, exist_ok=True)

    with rawpy.imread(str(source)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=use_camera_wb,
            output_bps=8,
            no_auto_bright=False,
        )

    image = Image.fromarray(rgb)
    image.save(
        destination,
        "JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
    )


def convert_heic_to_jpg(source: Path, destination: Path, quality: int) -> None:
    try:
        from PIL import Image, ImageOps
        from pillow_heif import register_heif_opener
    except ImportError as exc:
        raise RuntimeError(
            "缺少 HEIC 转换依赖。请先运行：pip install -r requirements.txt"
        ) from exc

    register_heif_opener()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")

        image.save(
            destination,
            "JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )


def convert_to_jpg(source: Path, destination: Path, quality: int, use_camera_wb: bool) -> None:
    suffix = source.suffix.lower()
    if suffix in RAW_SUFFIXES:
        convert_raw_to_jpg(source, destination, quality, use_camera_wb)
        return
    if suffix in HEIC_SUFFIXES:
        convert_heic_to_jpg(source, destination, quality)
        return
    raise RuntimeError(f"不支持的文件格式：{source.suffix}")


def convert_task(task: ConvertTask, quality: int, use_camera_wb: bool) -> ConvertResult:
    try:
        convert_to_jpg(task.source, task.destination, quality, use_camera_wb)
    except Exception as exc:
        return ConvertResult(task=task, error=str(exc), debug=traceback.format_exc())
    return ConvertResult(task=task)


class NefConverterApp(ttk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root, padding=14)
        self.root = root
        self.files: list[Path] = []
        self.worker: threading.Thread | None = None
        self.cancel_requested = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self.output_dir = StringVar(value="")
        self.quality = IntVar(value=95)
        self.worker_count = IntVar(value=CPU_COUNT)
        self.recursive = BooleanVar(value=True)
        self.overwrite = BooleanVar(value=False)
        self.use_camera_wb = BooleanVar(value=True)
        self.name_by_capture_time = BooleanVar(value=False)

        self._configure_root()
        self._build_ui()
        self._poll_events()

    def _configure_root(self) -> None:
        self.root.title("RAW / HEIC 转 JPG 工具")
        self.root.geometry("900x620")
        self.root.minsize(760, 520)

        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Danger.TButton", foreground="#8a1f11")

    def _build_ui(self) -> None:
        self.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="NEF / DNG / HEIC 批量转 JPG", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="选择文件或文件夹，设置输出目录后开始转换。",
            style="Hint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        toolbar = ttk.Frame(self)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        ttk.Button(toolbar, text="添加文件", command=self.add_files).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(toolbar, text="添加文件夹", command=self.add_folder).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Checkbutton(toolbar, text="包含子文件夹", variable=self.recursive).grid(
            row=0, column=2, padx=(0, 18)
        )
        ttk.Button(toolbar, text="移除选中", command=self.remove_selected).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(toolbar, text="清空列表", command=self.clear_files).grid(
            row=0, column=4, padx=(0, 8)
        )

        list_frame = ttk.Frame(self)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.file_list = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            activestyle="dotbox",
            font=("Consolas", 10),
        )
        self.file_list.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=scrollbar.set)

        options = ttk.LabelFrame(self, text="转换设置", padding=12)
        options.grid(row=3, column=0, sticky="ew", pady=(12, 8))
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="保存到").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(options, textvariable=self.output_dir).grid(row=0, column=1, sticky="ew")
        ttk.Button(options, text="选择路径", command=self.choose_output_dir).grid(
            row=0, column=2, padx=(8, 0)
        )

        ttk.Label(options, text="JPG 质量").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        quality_frame = ttk.Frame(options)
        quality_frame.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        quality_frame.columnconfigure(0, weight=1)

        ttk.Scale(
            quality_frame,
            from_=60,
            to=100,
            variable=self.quality,
            command=self._sync_quality_label,
        ).grid(row=0, column=0, sticky="ew")
        self.quality_label = ttk.Label(quality_frame, text="95")
        self.quality_label.grid(row=0, column=1, sticky="e", padx=(10, 0))

        ttk.Label(options, text="并行任务").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        worker_frame = ttk.Frame(options)
        worker_frame.grid(row=2, column=1, sticky="w", pady=(10, 0))
        ttk.Spinbox(
            worker_frame,
            from_=1,
            to=CPU_COUNT,
            textvariable=self.worker_count,
            width=6,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            worker_frame,
            text=f"最多 {CPU_COUNT}，数值越大速度越快但更占内存",
            style="Hint.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        checkboxes = ttk.Frame(options)
        checkboxes.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(10, 0))
        ttk.Checkbutton(checkboxes, text="RAW 使用相机白平衡", variable=self.use_camera_wb).grid(
            row=0, column=0, padx=(0, 12)
        )
        ttk.Checkbutton(checkboxes, text="覆盖已有 JPG", variable=self.overwrite).grid(
            row=0, column=1
        )
        ttk.Checkbutton(
            options,
            text="按拍摄时间命名（无元数据时用文件创建时间）",
            variable=self.name_by_capture_time,
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(10, 0))

        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)

        self.status_text = StringVar(value="等待选择待转换文件")
        ttk.Label(actions, textvariable=self.status_text).grid(row=0, column=0, sticky="w")

        self.start_button = ttk.Button(actions, text="开始转换", command=self.start_conversion)
        self.start_button.grid(row=0, column=1, padx=(8, 0))

        self.cancel_button = ttk.Button(
            actions,
            text="取消",
            command=self.cancel_conversion,
            state=tk.DISABLED,
        )
        self.cancel_button.grid(row=0, column=2, padx=(8, 0))

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.grid(row=5, column=0, sticky="ew", pady=(8, 8))

        log_frame = ttk.LabelFrame(self, text="日志", padding=8)
        log_frame.grid(row=6, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=8, wrap="word", state=tk.DISABLED)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

    def _sync_quality_label(self, _value: str | None = None) -> None:
        self.quality_label.configure(text=str(int(round(self.quality.get()))))

    def add_files(self) -> None:
        filenames = filedialog.askopenfilenames(
            title="选择待转换文件",
            filetypes=[
                ("支持的格式", "*.nef *.NEF *.nrw *.NRW *.dng *.DNG *.heic *.HEIC *.heif *.HEIF"),
                ("RAW 文件", "*.nef *.NEF *.nrw *.NRW *.dng *.DNG"),
                ("HEIC / HEIF", "*.heic *.HEIC *.heif *.HEIF"),
                ("所有文件", "*.*"),
            ],
        )
        self._add_paths(Path(name) for name in filenames)

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择包含待转换文件的文件夹")
        if not folder:
            return

        files = find_convertible_files(Path(folder), self.recursive.get())
        if not files:
            messagebox.showinfo("未找到文件", "这个文件夹里没有找到 .nef、.nrw、.dng、.heic 或 .heif 文件。")
            return

        self._add_paths(files)

    def _add_paths(self, paths) -> None:
        existing = {path.resolve() for path in self.files}
        added = 0

        for path in paths:
            path = path.resolve()
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
                continue
            if path in existing:
                continue
            self.files.append(path)
            existing.add(path)
            added += 1

        if added:
            self._refresh_file_list()
            self.log(f"已添加 {added} 个文件。")
        self._update_idle_status()

    def remove_selected(self) -> None:
        selected = set(self.file_list.curselection())
        if not selected:
            return

        self.files = [path for index, path in enumerate(self.files) if index not in selected]
        self._refresh_file_list()
        self._update_idle_status()

    def clear_files(self) -> None:
        self.files.clear()
        self._refresh_file_list()
        self._update_idle_status()

    def _refresh_file_list(self) -> None:
        self.file_list.delete(0, tk.END)
        for path in self.files:
            self.file_list.insert(tk.END, str(path))

    def choose_output_dir(self) -> None:
        folder = filedialog.askdirectory(title="选择 JPG 保存路径")
        if folder:
            self.output_dir.set(folder)

    def start_conversion(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        if not self.files:
            messagebox.showwarning("没有文件", "请先添加需要转换的文件。")
            return

        output_dir_text = self.output_dir.get().strip()
        if not output_dir_text:
            messagebox.showwarning("没有保存路径", "请选择 JPG 保存路径。")
            return
        output_dir = Path(output_dir_text)

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("保存路径不可用", f"无法创建或访问保存路径：\n{exc}")
            return

        tasks = self._build_tasks(output_dir)
        if not tasks:
            messagebox.showinfo("无需转换", "没有可转换的任务。")
            return

        worker_count = self._normalize_worker_count(len(tasks))
        self.cancel_requested.clear()
        self.start_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.progress.configure(maximum=len(tasks), value=0)
        self.status_text.set(f"准备转换 {len(tasks)} 个文件，并行任务 {worker_count} 个...")
        self.log(f"开始转换，共 {len(tasks)} 个文件，并行任务 {worker_count} 个。")

        self.worker = threading.Thread(
            target=self._convert_worker,
            args=(tasks, int(round(self.quality.get())), self.use_camera_wb.get(), worker_count),
            daemon=True,
        )
        self.worker.start()

    def _normalize_worker_count(self, task_count: int) -> int:
        try:
            requested = int(self.worker_count.get())
        except (TypeError, ValueError, tk.TclError):
            requested = CPU_COUNT

        normalized = max(1, min(requested, CPU_COUNT))
        if requested != normalized:
            self.worker_count.set(normalized)
        return min(normalized, task_count)

    def _build_tasks(self, output_dir: Path) -> list[ConvertTask]:
        tasks: list[ConvertTask] = []
        used_destinations: set[Path] = set()

        for source in self.files:
            destination = self._unique_destination(source, output_dir, used_destinations)
            used_destinations.add(destination.resolve())
            tasks.append(ConvertTask(source=source, destination=destination))

        return tasks

    def _unique_destination(
        self,
        source: Path,
        output_dir: Path,
        used_destinations: set[Path],
    ) -> Path:
        destination_stem = (
            datetime_name_for_source(source) if self.name_by_capture_time.get() else source.stem
        )
        candidate = output_dir / f"{destination_stem}.jpg"
        if not self.overwrite.get():
            candidate = next_available_path(candidate)
        if candidate.resolve() not in used_destinations:
            return candidate

        index = 1
        while True:
            candidate = output_dir / f"{destination_stem}_{index}.jpg"
            if not self.overwrite.get():
                candidate = next_available_path(candidate)
            if candidate.resolve() not in used_destinations:
                return candidate
            index += 1

    def _convert_worker(
        self,
        tasks: list[ConvertTask],
        quality: int,
        use_camera_wb: bool,
        worker_count: int,
    ) -> None:
        completed = 0
        failed = 0
        processed = 0
        executor = ProcessPoolExecutor(max_workers=worker_count)

        try:
            futures = {
                executor.submit(convert_task, task, quality, use_camera_wb): task
                for task in tasks
            }
            pending = set(futures)

            while pending:
                if self.cancel_requested.is_set():
                    for future in pending:
                        future.cancel()
                    self.events.put(("cancelled", (completed, failed)))
                    return

                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    processed += 1
                    task = futures[future]

                    try:
                        result = future.result()
                    except Exception as exc:
                        failed += 1
                        self.events.put(("log", f"失败：{task.source}\n原因：{exc}"))
                        self.events.put(("debug", traceback.format_exc()))
                    else:
                        if result.error:
                            failed += 1
                            self.events.put(("log", f"失败：{result.task.source}\n原因：{result.error}"))
                            self.events.put(("debug", result.debug))
                        else:
                            completed += 1
                            self.events.put(
                                ("log", f"完成：{result.task.source.name} -> {result.task.destination}")
                            )

                    self.events.put(("progress", processed))
                    self.events.put(
                        (
                            "status",
                            f"[{processed}/{len(tasks)}] 已完成，正在并行转换 {min(worker_count, len(pending))} 个任务",
                        )
                    )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        self.events.put(("finished", (completed, failed)))

    def cancel_conversion(self) -> None:
        self.cancel_requested.set()
        self.cancel_button.configure(state=tk.DISABLED)
        self.status_text.set("正在取消，等待当前文件处理完成...")

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                self.status_text.set(str(payload))
            elif kind == "progress":
                self.progress.configure(value=int(payload))
            elif kind == "log":
                self.log(str(payload))
            elif kind == "debug":
                self.log(str(payload), visible=False)
            elif kind == "finished":
                completed, failed = payload
                self._finish_conversion(
                    f"转换完成：成功 {completed} 个，失败 {failed} 个。"
                )
            elif kind == "cancelled":
                completed, failed = payload
                self._finish_conversion(
                    f"已取消：成功 {completed} 个，失败 {failed} 个。"
                )

        self.root.after(100, self._poll_events)

    def _finish_conversion(self, message: str) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.status_text.set(message)
        self.log(message)
        messagebox.showinfo("转换结束", message)

    def log(self, message: str, visible: bool = True) -> None:
        if not visible:
            message = f"[调试信息]\n{message}"

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message.rstrip() + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _update_idle_status(self) -> None:
        count = len(self.files)
        if count:
            self.status_text.set(f"已选择 {count} 个文件")
        else:
            self.status_text.set("等待选择待转换文件")


def main() -> None:
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = NefConverterApp(root)
    app.mainloop()


if __name__ == "__main__":
    main()
