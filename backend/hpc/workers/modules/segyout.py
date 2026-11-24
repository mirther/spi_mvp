import os
import struct
import uuid

from pathlib import Path, PurePosixPath

import numpy as np
import segyio

from workers.runtime.working_dataset import WorkingDataset


# workers/modules/segyout.py -> SPI root is parents[2].
SPI_ROOT = Path(__file__).resolve().parents[2]

OUTPUT_FORMAT_CODE = 5  # IEEE Float32


# --------------------------------------------------
# OUTPUT PATH
# --------------------------------------------------

def resolve_output_path(relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)

    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid output path.")

    # The only permitted destination is storage/data/<filename>.
    if len(relative.parts) != 3 or relative.parts[:2] != ("storage", "data"):
        raise ValueError("Output must be a filename directly inside storage/data.")

    storage_root = (SPI_ROOT / "storage" / "data").resolve()
    output_path = (storage_root / relative.parts[2]).resolve()

    if output_path.parent != storage_root:
        raise ValueError("Output must be directly inside storage/data.")

    if output_path.suffix.lower() not in {".sgy", ".segy"}:
        raise ValueError("Output must have .sgy or .segy extension.")

    if not storage_root.is_dir():
        raise FileNotFoundError("The fixed storage/data directory does not exist.")

    return output_path


def ensure_output_is_not_source(output_path: Path, dataset: WorkingDataset) -> None:
    """Protect the input file loaded into this WorkingDataset from replacement."""
    source_path = (SPI_ROOT / dataset.source_path).resolve()
    if output_path == source_path:
        raise ValueError(
            "SEGYOUT cannot overwrite the SEG-Y source currently loaded by SEGYIN. "
            "Choose a different output filename."
        )


def publish_completed_output(temporary_path: Path, output_path: Path) -> None:
    """Atomically publish a fully written and verified SEG-Y file.

    Both paths are created in the same directory/filesystem. os.replace()
    replaces an existing destination atomically on the HPC POSIX filesystem.
    The previous destination remains intact until this final operation.
    """
    os.replace(temporary_path, output_path)


# --------------------------------------------------
# DATA VALIDATION
# --------------------------------------------------

def validate_dataset(dataset: WorkingDataset) -> tuple[
    np.ndarray,
    np.ndarray,
    int,
    int,
    int,
    str,
]:

    samples = np.asarray(
        dataset.samples,
        dtype=np.float32,
    )

    headers = np.asarray(dataset.trace_headers)

    if samples.ndim != 2:
        raise ValueError("Samples must be a 2D array.")

    trace_count, samples_per_trace = samples.shape

    if trace_count == 0 or samples_per_trace == 0:
        raise ValueError("Dataset contains no seismic samples.")

    if headers.shape != (trace_count, 240):
        raise ValueError(
            "Trace header array must have shape "
            "(trace_count, 240)."
        )

    if headers.dtype != np.uint8:
        raise ValueError(
            "Trace headers must use uint8."
        )

    if len(dataset.binary_header) != 400:
        raise ValueError("Binary header must be exactly 400 bytes.")

    if not dataset.textual_headers:
        raise ValueError("At least one textual header is required.")

    for index, header in enumerate(dataset.textual_headers):
        if len(header) != 3200:
            raise ValueError(
                f"Textual header {index} must be exactly 3200 bytes."
            )

    endian = dataset.metadata.get("endian", "big")

    if endian not in {"big", "little"}:
        raise ValueError("Unsupported byte order.")

    sample_interval_us = int(
        dataset.metadata.get("sample_interval_us", 0)
    )

    if not 0 <= sample_interval_us <= 65535:
        raise ValueError("Sample interval exceeds initial SEG-Y limits.")

    if samples_per_trace > 65535:
        raise ValueError(
            "More than 65535 samples per trace is not supported yet."
        )

    extended_header_count = len(dataset.textual_headers) - 1

    if extended_header_count > 32767:
        raise ValueError("Too many extended textual headers.")

    return (
        samples,
        headers,
        trace_count,
        samples_per_trace,
        sample_interval_us,
        endian,
    )


# --------------------------------------------------
# WRITE ORIGINAL REEL HEADERS AND RAW TRACE HEADERS
# --------------------------------------------------

def write_raw_headers(
    output_path: Path,
    dataset: WorkingDataset,
    headers: np.ndarray,
    samples_per_trace: int,
    sample_interval_us: int,
    endian: str,
) -> None:

    byte_order = ">" if endian == "big" else "<"

    binary_header = bytearray(dataset.binary_header)

    extended_header_count = len(
        dataset.textual_headers
    ) - 1

    # Update the binary reel header to describe the output.
    #
    # Binary header offsets are zero-based relative
    # to the beginning of the 400-byte binary header.

    # Sample interval: SEG-Y bytes 3217-3218
    if sample_interval_us > 0:
        struct.pack_into(
            f"{byte_order}H",
            binary_header,
            16,
            sample_interval_us,
        )

    # Samples per trace: SEG-Y bytes 3221-3222
    struct.pack_into(
        f"{byte_order}H",
        binary_header,
        20,
        samples_per_trace,
    )

    # Sample format code: SEG-Y bytes 3225-3226
    struct.pack_into(
        f"{byte_order}H",
        binary_header,
        24,
        OUTPUT_FORMAT_CODE,
    )

    # Number of extended textual headers: SEG-Y bytes 3505-3506
    struct.pack_into(
        f"{byte_order}h",
        binary_header,
        304,
        extended_header_count,
    )

    samples_bytes = 4  # IEEE Float32

    first_trace_offset = (
        3600 + extended_header_count * 3200
    )

    trace_record_size = (
        240 + samples_per_trace * samples_bytes
    )

    with output_path.open("r+b") as file:

        # ------------------------------------------
        # TEXTUAL HEADERS
        # ------------------------------------------

        for index, header in enumerate(
            dataset.textual_headers
        ):
            file.seek(index * 3200)
            file.write(header)

        # ------------------------------------------
        # BINARY HEADER
        # ------------------------------------------

        file.seek(3200)
        file.write(binary_header)

        # ------------------------------------------
        # TRACE HEADERS
        # ------------------------------------------

        for trace_index in range(len(headers)):

            offset = (
                first_trace_offset
                + trace_index * trace_record_size
            )

            file.seek(offset)

            file.write(
                headers[trace_index].tobytes()
            )

        file.flush()
        os.fsync(file.fileno())


# --------------------------------------------------
# OUTPUT VALIDATION
# --------------------------------------------------

def validate_output(
    output_path: Path,
    dataset: WorkingDataset,
    samples: np.ndarray,
    headers: np.ndarray,
    trace_count: int,
    samples_per_trace: int,
    endian: str,
) -> None:

    with segyio.open(
        str(output_path),
        "r",
        ignore_geometry=True,
        endian=endian,
    ) as segy:

        if segy.tracecount != trace_count:
            raise ValueError("Output trace count mismatch.")

        if len(segy.samples) != samples_per_trace:
            raise ValueError("Output sample count mismatch.")

        if int(segy.bin[segyio.BinField.Format]) != OUTPUT_FORMAT_CODE:
            raise ValueError("Output sample format mismatch.")

        # Verify first and last amplitude traces.
        for index in {0, trace_count - 1}:

            written = np.asarray(segy.trace[index])

            if not np.array_equal(written, samples[index]):
                raise ValueError(
                    f"Amplitude verification failed at trace {index}."
                )

    # Verify original raw trace headers.
    extended_header_count = len(dataset.textual_headers) - 1

    first_trace_offset = (
        3600 + extended_header_count * 3200
    )

    trace_record_size = 240 + samples_per_trace * 4

    with output_path.open("rb") as file:

        for index in {0, trace_count - 1}:

            file.seek(
                first_trace_offset + index * trace_record_size
            )

            written_header = file.read(240)

            if written_header != headers[index].tobytes():
                raise ValueError(
                    f"Trace header verification failed at trace {index}."
                )


# --------------------------------------------------
# SEGYOUT
# --------------------------------------------------

def execute(
    dataset: WorkingDataset,
    parameters: dict,
    context=None,
) -> dict:

    output_path = resolve_output_path(
        parameters["path"]
    )

    # Existing outputs may be replaced, but never replace the source file
    # currently loaded by SEGYIN in this pipeline.
    ensure_output_is_not_source(output_path, dataset)

    (
        samples,
        headers,
        trace_count,
        samples_per_trace,
        sample_interval_us,
        endian,
    ) = validate_dataset(dataset)

    extended_header_count = (
        len(dataset.textual_headers) - 1
    )

    temporary_path = output_path.with_name(
        f".{output_path.stem}.{uuid.uuid4().hex}.tmp{output_path.suffix}"
    )

    try:

        # ------------------------------------------
        # CREATE NEW SEG-Y FILE
        # ------------------------------------------

        spec = segyio.spec()

        spec.tracecount = trace_count

        spec.samples = np.arange(
            samples_per_trace,
            dtype=np.int32,
        )

        spec.format = OUTPUT_FORMAT_CODE

        spec.ext_headers = extended_header_count

        spec.endian = endian

        with segyio.create(
            str(temporary_path),
            spec,
        ) as segy:

            for index in range(trace_count):
                segy.trace[index] = samples[index]

        # ------------------------------------------
        # WRITE ORIGINAL HEADERS
        # ------------------------------------------

        write_raw_headers(
            output_path=temporary_path,
            dataset=dataset,
            headers=headers,
            samples_per_trace=samples_per_trace,
            sample_interval_us=sample_interval_us,
            endian=endian,
        )

        # ------------------------------------------
        # VERIFY RESULT
        # ------------------------------------------

        validate_output(
            output_path=temporary_path,
            dataset=dataset,
            samples=samples,
            headers=headers,
            trace_count=trace_count,
            samples_per_trace=samples_per_trace,
            endian=endian,
        )

        # ------------------------------------------
        # PUBLISH COMPLETED FILE
        # ------------------------------------------

        # Replace an existing output only after the new SEG-Y has been
        # completely written and verified. Because the temporary file lives
        # beside the destination, os.replace() is atomic on the HPC filesystem.
        publish_completed_output(temporary_path, output_path)

    except Exception:

        if temporary_path.exists():
            temporary_path.unlink()

        raise

    return {
        "status": "success",
        "output_path": output_path.relative_to(
            SPI_ROOT
        ).as_posix(),
        "file_size_bytes": output_path.stat().st_size,
        "trace_count": trace_count,
        "samples_per_trace": samples_per_trace,
        "sample_format_code": OUTPUT_FORMAT_CODE,
    }