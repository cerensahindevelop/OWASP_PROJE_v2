"""BOM-stripping codecs whose encoders retain explicit UTF byte order.

Names are plain strings, so audit records and encoding metadata can persist
them across processes. They implement Python's standard codec interface.
"""
import codecs

_FORMATS = {
    "utf_16_le_sig": ("utf-16-le", "utf-16", codecs.BOM_UTF16_LE),
    "utf_16_be_sig": ("utf-16-be", "utf-16", codecs.BOM_UTF16_BE),
    "utf_32_le_sig": ("utf-32-le", "utf-32", codecs.BOM_UTF32_LE),
    "utf_32_be_sig": ("utf-32-be", "utf-32", codecs.BOM_UTF32_BE),
}


def _search(name):
    if name not in _FORMATS:
        return None
    encoding, family, bom = _FORMATS[name]
    def encode(value, errors="strict"):
        return bom + value.encode(encoding, errors), len(value)
    def decode(value, errors="strict"):
        raw = bytes(value)
        return raw.decode(family, errors), len(raw)
    class Encoder(codecs.IncrementalEncoder):
        def __init__(self, errors="strict"):
            super().__init__(errors)
            self.first = True
        def encode(self, value, final=False):
            prefix = bom if self.first else b""
            self.first = False
            return prefix + value.encode(encoding, self.errors)
        def reset(self):
            self.first = True
        def getstate(self):
            return int(self.first)
        def setstate(self, state):
            self.first = bool(state)
    return codecs.CodecInfo(name=name, encode=encode, decode=decode,
        incrementalencoder=Encoder, incrementaldecoder=codecs.getincrementaldecoder(family))


codecs.register(_search)
