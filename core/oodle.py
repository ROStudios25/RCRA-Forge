# core/oodle.py
# Oodle LZ decompression wrapper for RCRA Forge.
# Requires oo2core_9_win64.dll in the working directory (Windows only).
# OodleLZ_Decompress signature:
#   OodleLZ_Decompress(
#       const void* compBuf, OO_SINTa compBufSize,
#       void* rawBuf,        OO_SINTa rawLen,
#       OodleLZ_FuzzSafe fuzzSafe,
#       OodleLZ_CheckCRC checkCRC,
#       OodleLZ_Verbosity verbosity,
#       void* decBufBase,    OO_SINTa decBufSize,
#       void* fpCallback,    void* callbackUserData,
#       void* decoderMemory, OO_SINTa decoderMemorySize,
#       OodleLZ_Decode_ThreadPhase threadPhase
#   ) -> OO_SINTa (bytes decoded, or negative on error)

import ctypes
import os
import platform

lib = None
if platform.system() == "Windows":
    try:
        lib = ctypes.windll.LoadLibrary(
            os.path.join(os.getcwd(), "oo2core_9_win64.dll")
        )
        lib.OodleLZ_Decompress.restype  = ctypes.c_int64
        lib.OodleLZ_Decompress.argtypes = [
            ctypes.c_char_p,   # compBuf
            ctypes.c_int64,    # compBufSize
            ctypes.c_char_p,   # rawBuf
            ctypes.c_int64,    # rawLen
            ctypes.c_int32,    # fuzzSafe       (1 = yes)
            ctypes.c_int32,    # checkCRC       (0 = no)
            ctypes.c_int32,    # verbosity      (0 = none)
            ctypes.c_char_p,   # decBufBase     (NULL)
            ctypes.c_int64,    # decBufSize     (0)
            ctypes.c_char_p,   # fpCallback     (NULL)
            ctypes.c_char_p,   # callbackUserData (NULL)
            ctypes.c_char_p,   # decoderMemory  (NULL)
            ctypes.c_int64,    # decoderMemorySize (0)
            ctypes.c_int32,    # threadPhase    (3 = all)
        ]
    except Exception as e:
        print(f"[oodle] failed to load oo2core_9_win64.dll: {e}")


def decompress(compressed: bytes, output_size: int) -> bytearray:
    """
    Decompress Oodle-compressed data.

    Parameters
    ----------
    compressed  : raw compressed bytes
    output_size : expected decompressed size in bytes

    Returns
    -------
    bytearray of length output_size, or zeroed buffer if dll unavailable.
    """
    output = bytearray(output_size)
    if lib is None:
        print("[oodle] dll not loaded — returning zeroed buffer")
        return output

    # Use c_char array (not c_char_p) to avoid null-termination truncation.
    # c_char_p treats the buffer as a C string and stops at the first 0x00 byte,
    # which would silently truncate binary compressed data.
    comp_buf = (ctypes.c_char * len(compressed)).from_buffer_copy(compressed)
    out_buf  = (ctypes.c_char * output_size).from_buffer(output)

    result = lib.OodleLZ_Decompress(
        comp_buf,             # compBuf
        len(compressed),      # compBufSize
        out_buf,              # rawBuf
        output_size,          # rawLen
        1,                    # fuzzSafe
        0,                    # checkCRC
        0,                    # verbosity
        None,                 # decBufBase
        0,                    # decBufSize
        None,                 # fpCallback
        None,                 # callbackUserData
        None,                 # decoderMemory
        0,                    # decoderMemorySize
        3,                    # threadPhase (OodleLZ_Decode_ThreadPhase_All)
    )

    if result < 0:
        print(f"[oodle] decompression failed: result={result}")
    elif result != output_size:
        print(f"[oodle] size mismatch: got {result}, expected {output_size}")

    return output
