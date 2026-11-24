from pathlib import Path, PurePosixPath

import numpy as np
import segyio

# workers/modules/segyin.py -> SPI root is parents[2].
SPI_ROOT = Path(__file__).resolve().parents[2]

from workers.runtime.working_dataset import WorkingDataset


# --------------------------------------------------
# PATH RESOLUTION
# --------------------------------------------------

def resolve_segy_path(relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)

    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid SEG-Y path.")

    # Only files directly inside storage/data are selectable. No subfolders.
    if len(relative.parts) != 3 or relative.parts[:2] != ("storage", "data"):
        raise ValueError("File must be directly inside storage/data.")

    storage_root = (
        SPI_ROOT / "storage" / "data"
    ).resolve()

    file_path = (
        SPI_ROOT.joinpath(*relative.parts)
    ).resolve(strict=True)

    if not file_path.is_relative_to(storage_root):
        raise ValueError("File is outside SEG-Y storage.")

    if not file_path.is_file():
        raise ValueError("Path is not a file.")

    if file_path.suffix.lower() not in {".sgy", ".segy"}:
        raise ValueError("Unsupported file extension.")

    return file_path


# --------------------------------------------------
# REEL HEADERS
# --------------------------------------------------

def read_reel_headers(
    file_path: Path,
    extended_header_count: int,
) -> tuple[tuple[bytes, ...], bytes]:

    if extended_header_count < 0:
        raise NotImplementedError(
            "Variable-count extended textual headers "
            "are not supported yet."
        )

    textual_headers = []

    with file_path.open("rb") as file:

        # Main textual header: 3200 bytes
        main_textual_header = file.read(3200)

        if len(main_textual_header) != 3200:
            raise ValueError("Incomplete textual header.")

        textual_headers.append(main_textual_header)

        # Binary reel header: 400 bytes
        binary_header = file.read(400)

        if len(binary_header) != 400:
            raise ValueError("Incomplete binary header.")

        # Extended textual headers: 3200 bytes each
        for _ in range(extended_header_count):

            header = file.read(3200)

            if len(header) != 3200:
                raise ValueError(
                    "Incomplete extended textual header."
                )

            textual_headers.append(header)

    return tuple(textual_headers), binary_header


# --------------------------------------------------
# SEGYIN
# --------------------------------------------------

def execute(
    parameters: dict,
    context=None,
) -> WorkingDataset:

    # Resolve source file.
    relative_path = parameters["path"]

    file_path = resolve_segy_path(relative_path)

    # Open SEG-Y without requiring structured geometry.
    with segyio.open(
        str(file_path),
        "r",
        ignore_geometry=True,
    ) as segy:

        trace_count = int(segy.tracecount)

        if trace_count == 0:
            raise ValueError("SEG-Y file contains no traces.")

        # ------------------------------------------
        # READ AMPLITUDE SAMPLES
        # ------------------------------------------

        # Eagerly read all traces into RAM.
        samples = np.array(
            segy.trace.raw[:],
            copy=True,
        )

        if samples.ndim != 2:
            raise ValueError(
                "Expected a two-dimensional trace array."
            )

        if samples.shape[0] != trace_count:
            raise ValueError(
                "Amplitude trace count mismatch."
            )

        # ------------------------------------------
        # READ ALL RAW TRACE HEADERS
        # ------------------------------------------

        trace_headers = np.empty(
            (trace_count, 240),
            dtype=np.uint8,
        )

        for trace_index in range(trace_count):

            header = segy.header[trace_index]

            # Copy the complete original 240-byte header.
            raw_header = bytes(header.buf)

            if len(raw_header) != 240:
                raise ValueError(
                    f"Invalid trace header at index {trace_index}."
                )

            trace_headers[trace_index] = np.frombuffer(
                raw_header,
                dtype=np.uint8,
            )

        # ------------------------------------------
        # READ BINARY HEADER INFORMATION
        # ------------------------------------------

        sample_interval_us = int(
            segy.bin[segyio.BinField.Interval]
        )

        declared_samples = int(
            segy.bin[segyio.BinField.Samples]
        )

        sample_format_code = int(
            segy.bin[segyio.BinField.Format]
        )

        extended_header_count = int(segy.ext_headers)

        # ------------------------------------------
        # FILE METADATA
        # ------------------------------------------

        metadata = {
            "endian": "big",
            
            "trace_count": trace_count,

            "samples_per_trace": samples.shape[1],

            "declared_samples_per_trace": (
                declared_samples or None
            ),

            "sample_interval_us": sample_interval_us,

            "sample_interval_seconds": (
                sample_interval_us / 1_000_000
                if sample_interval_us > 0
                else None
            ),

            "sample_format_code": sample_format_code,

            "sample_dtype": str(samples.dtype),

            "extended_header_count": extended_header_count,
        }

    # ----------------------------------------------
    # READ ORIGINAL REEL HEADERS AS RAW BYTES
    # ----------------------------------------------

    textual_headers, binary_header = read_reel_headers(
        file_path,
        extended_header_count,
    )

    # ----------------------------------------------
    # CONSTRUCT WORKING DATASET
    # ----------------------------------------------

    dataset = WorkingDataset(
        source_path=file_path.relative_to(
            SPI_ROOT
        ).as_posix(),

        samples=samples,

        trace_headers=trace_headers,

        trace_indices=np.arange(
            trace_count,
            dtype=np.int64,
        ),

        textual_headers=textual_headers,

        binary_header=binary_header,

        metadata=metadata,
    )

    return dataset