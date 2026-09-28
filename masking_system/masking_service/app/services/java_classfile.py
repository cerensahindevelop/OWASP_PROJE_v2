"""Lossless Java constant-pool text adapter; never loads or executes user classes.

JVMS 4.4/4.7: https://docs.oracle.com/javase/specs/jvms/se25/html/jvms-4.html
Only string constants, annotation strings and SourceFile text can change.
Structural identifiers and opaque/unknown attributes fail closed. Numeric
constants, bytecode-created strings and encrypted payloads are outside text
coverage, which callers must report explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import struct

JAVA_CLASS_ENCODING = "java-class-v1"
CLASS_COVERAGE = (
    "Java .class: sabit havuzundaki metinler tarandı; string sabitleri, anotasyon "
    "metinleri ve kaynak dosya adı maskelenebilir. Sayısal sabitler, bytecode ile "
    "çalışma anında üretilen veya şifreli veriler bu metin taramasının kapsamında değildir. "
    "Sınıf çalıştırılmadı; uygulamanın çalışma davranışı garanti edilmez."
)


class ClassFormatError(ValueError):
    pass


def decode_mutf8(raw: bytes) -> str:
    units = bytearray()
    pos = 0
    while pos < len(raw):
        first = raw[pos]
        pos += 1
        if 1 <= first <= 0x7F:
            value = first
        elif 0xC0 <= first <= 0xDF:
            if pos >= len(raw) or raw[pos] & 0xC0 != 0x80:
                raise ClassFormatError("Java modified UTF-8 dizisi geçersiz")
            value = ((first & 31) << 6) | (raw[pos] & 63)
            pos += 1
            if value < 0x80 and not (first == 0xC0 and value == 0):
                raise ClassFormatError("Java modified UTF-8 dizisi kanonik değil")
        elif 0xE0 <= first <= 0xEF:
            if pos + 1 >= len(raw) or any(x & 0xC0 != 0x80 for x in raw[pos:pos+2]):
                raise ClassFormatError("Java modified UTF-8 dizisi geçersiz")
            value = ((first & 15) << 12) | ((raw[pos] & 63) << 6) | (raw[pos+1] & 63)
            pos += 2
            if value < 0x800:
                raise ClassFormatError("Java modified UTF-8 dizisi kanonik değil")
        else:
            raise ClassFormatError("Java modified UTF-8 başlangıç baytı geçersiz")
        units.extend(value.to_bytes(2, "big"))
    try:
        return units.decode("utf-16-be")
    except UnicodeDecodeError:
        raise ClassFormatError("Eşleşmeyen Java surrogate karakteri desteklenmiyor") from None


def encode_mutf8(text: str) -> bytes:
    try:
        raw = text.encode("utf-16-be")
    except UnicodeEncodeError:
        raise ClassFormatError("Java metninde eşleşmeyen surrogate karakteri var") from None
    result = bytearray()
    for pos in range(0, len(raw), 2):
        value = int.from_bytes(raw[pos:pos+2], "big")
        if 1 <= value <= 127:
            result.append(value)
        elif value <= 2047:
            result.extend((0xC0 | (value >> 6), 0x80 | (value & 63)))
        else:
            result.extend((0xE0 | (value >> 12), 0x80 | ((value >> 6) & 63), 0x80 | (value & 63)))
    if len(result) > 65535:
        raise ClassFormatError("Maskelenmiş Java metni 65535 bayt sınırını aşıyor")
    return bytes(result)


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise ClassFormatError("Java class dosyası kesik veya uzunluk alanı geçersiz")
        data = self.data[self.pos:self.pos+count]
        self.pos += count
        return data

    def u1(self):
        return int.from_bytes(self.take(1), "big")

    def u2(self):
        return int.from_bytes(self.take(2), "big")

    def u4(self):
        return int.from_bytes(self.take(4), "big")

    def done(self):
        if self.pos != len(self.data):
            raise ClassFormatError("Java class yapısında açıklanamayan ek baytlar var")


@dataclass(frozen=True)
class JavaClass:
    raw: bytes
    # (constant pool index, start of u2 length, end of string payload, value)
    strings: tuple[tuple[int, int, int, str], ...]
    editable: frozenset[int]
    labels: tuple[str, ...]

    @property
    def text(self) -> str:
        # Preserve ConstantValue field names as detection context: a bare
        # secret string is much harder to recognize than PASSWORD: value.
        return json.dumps([{label: entry[3]} for label, entry in zip(self.labels, self.strings)],
                          ensure_ascii=False, indent=2)

    def rebuild(self, text: str) -> bytes:
        try:
            entries = json.loads(text)
        except (ValueError, RecursionError):
            raise ClassFormatError("Java metin görünümü geçerli JSON değil") from None
        if not isinstance(entries, list) or len(entries) != len(self.strings):
            raise ClassFormatError("Java sabit havuzu metin sayısı veya türü değişti")
        values = []
        for entry, label in zip(entries, self.labels):
            if not isinstance(entry, dict) or list(entry) != [label] or not isinstance(entry[label], str):
                raise ClassFormatError("Java sabit havuzu metin yapısı/alan bağlamı değişti")
            values.append(entry[label])
        parts, cursor = [], 0
        for (index, start, end, original), value in zip(self.strings, values):
            if value == original:
                continue
            if index not in self.editable:
                raise ClassFormatError(
                    f"Java yapısal adında/tanımında hassas bulgu var (sabit #{index}); "
                    "güvenli bytecode yeniden adlandırması desteklenmediği için çıktı engellendi"
                )
            encoded = encode_mutf8(value)
            parts.extend((self.raw[cursor:start], struct.pack('>H', len(encoded)), encoded))
            cursor = end
        parts.append(self.raw[cursor:])
        result = b''.join(parts)
        # Verify lengths, references, boundaries and text encoding again.
        parsed = parse_class(result)
        if [s[3] for s in parsed.strings] != values:
            raise ClassFormatError("Java class yeniden oluşturma doğrulaması başarısız")
        return result


def parse_class(raw: bytes) -> JavaClass:
    r = _Reader(raw)
    if r.take(4) != b'\xca\xfe\xba\xbe':
        raise ClassFormatError(
            "Dosyanın başında geçerli bir Java .class dosya imzası bulunamadığı için "
            "işlenemedi; dosya bozulmuş, yanlış uzantıyla kaydedilmiş ya da gerçekte "
            "bir .class dosyası olmayabilir."
        )
    minor, major, count = r.u2(), r.u2(), r.u2()
    if not 45 <= major <= 70 or count < 1:
        raise ClassFormatError("Java class sürümü veya sabit havuzu boyutu desteklenmiyor")
    pool, strings = {}, []
    index = 1
    sizes = {3:4, 4:4, 5:8, 6:8, 7:2, 8:2, 9:4, 10:4, 11:4, 12:4, 15:3, 16:2, 17:4, 18:4, 19:2, 20:2}
    while index < count:
        tag = r.u1()
        if tag == 1:
            start = r.pos
            value = decode_mutf8(r.take(r.u2()))
            strings.append((index, start, r.pos, value))
            pool[index] = (tag, value)
        elif tag in sizes:
            pool[index] = (tag, r.take(sizes[tag]))
        else:
            raise ClassFormatError("Java sabit havuzunda bilinmeyen kayıt türü")
        if tag in (5, 6):
            if index + 1 >= count:
                raise ClassFormatError("Java long/double sabiti ikinci yuvası eksik")
            index += 1
        index += 1
    editable, protected, field_labels = set(), set(), {}

    def ref(index, tags, *, protect=False, zero=False):
        if zero and index == 0:
            return None
        item = pool.get(index)
        if item is None or item[0] not in tags:
            raise ClassFormatError("Java sabit havuzu başvurusu geçersiz")
        if protect and item[0] == 1:
            protected.add(index)
        return item[1]

    for tag, data in pool.values():
        if tag in (7, 8, 16, 19, 20):
            idx = int.from_bytes(data, 'big')
            ref(idx, (1,), protect=tag != 8)
            if tag == 8:
                editable.add(idx)
        elif tag in (9, 10, 11):
            a, b = struct.unpack('>HH', data)
            ref(a, (7,)); ref(b, (12,))
        elif tag == 12:
            for idx in struct.unpack('>HH', data):
                ref(idx, (1,), protect=True)
        elif tag in (17, 18):
            ref(int.from_bytes(data[2:], 'big'), (12,))
        elif tag == 15:
            kind = data[0]
            if not 1 <= kind <= 9:
                raise ClassFormatError("Java method handle türü geçersiz")
            ref(int.from_bytes(data[1:], 'big'), (9,) if kind <= 4 else ((11,) if kind == 9 else (10,11)))

    def annotation(a, depth):
        if depth > 32:
            raise ClassFormatError("Java anotasyon iç içe sınırı aşıldı")
        ref(a.u2(), (1,), protect=True)
        for _ in range(a.u2()):
            ref(a.u2(), (1,), protect=True)
            element(a, depth+1)

    def element(a, depth):
        if depth > 32:
            raise ClassFormatError("Java anotasyon iç içe sınırı aşıldı")
        tag = chr(a.u1())
        if tag == 's':
            idx = a.u2(); ref(idx, (1,)); editable.add(idx)
        elif tag in 'BCISZ': ref(a.u2(), (3,))
        elif tag == 'D': ref(a.u2(), (6,))
        elif tag == 'F': ref(a.u2(), (4,))
        elif tag == 'J': ref(a.u2(), (5,))
        elif tag == 'c': ref(a.u2(), (1,), protect=True)
        elif tag == 'e':
            ref(a.u2(), (1,), protect=True); ref(a.u2(), (1,), protect=True)
        elif tag == '@': annotation(a, depth+1)
        elif tag == '[':
            for _ in range(a.u2()): element(a, depth+1)
        else: raise ClassFormatError("Java anotasyon değeri desteklenmiyor")

    def attributes(a, depth=0, field_name=None):
        if depth > 32:
            raise ClassFormatError("Java öznitelik iç içe sınırı aşıldı")
        for _ in range(a.u2()):
            name = ref(a.u2(), (1,), protect=True)
            attr = _Reader(a.take(a.u4()))
            if name == 'ConstantValue':
                idx = attr.u2()
                data = ref(idx, (3,4,5,6,8))
                if field_name is not None and pool[idx][0] == 8:
                    field_labels.setdefault(int.from_bytes(data, 'big'), field_name)
            elif name == 'Code':
                attr.u2(); attr.u2()
                length = attr.u4()
                if not 0 < length < 65536: raise ClassFormatError("Java Code uzunluğu geçersiz")
                attr.take(length)
                for _ in range(attr.u2()):
                    start, end, handler, catch = attr.u2(), attr.u2(), attr.u2(), attr.u2()
                    if not start < end <= length or handler >= length: raise ClassFormatError("Java exception aralığı geçersiz")
                    ref(catch, (7,), zero=True)
                attributes(attr, depth+1)
            elif name in ('Synthetic', 'Deprecated'): pass
            elif name == 'SourceFile':
                idx = attr.u2(); ref(idx, (1,)); editable.add(idx)
            elif name == 'Signature': ref(attr.u2(), (1,), protect=True)
            elif name in ('Exceptions','NestMembers','PermittedSubclasses','ModulePackages'):
                for _ in range(attr.u2()): ref(attr.u2(), (20,) if name == 'ModulePackages' else (7,))
            elif name in ('NestHost','ModuleMainClass'): ref(attr.u2(), (7,))
            elif name == 'EnclosingMethod':
                ref(attr.u2(), (7,)); ref(attr.u2(), (12,), zero=True)
            elif name == 'InnerClasses':
                for _ in range(attr.u2()):
                    ref(attr.u2(), (7,)); ref(attr.u2(), (7,), zero=True)
                    ref(attr.u2(), (1,), protect=True, zero=True); attr.u2()
            elif name in ('LocalVariableTable','LocalVariableTypeTable'):
                for _ in range(attr.u2()):
                    attr.u2(); attr.u2()
                    ref(attr.u2(), (1,), protect=True); ref(attr.u2(), (1,), protect=True); attr.u2()
            elif name == 'MethodParameters':
                for _ in range(attr.u1()):
                    ref(attr.u2(), (1,), protect=True, zero=True); attr.u2()
            elif name in ('RuntimeVisibleAnnotations','RuntimeInvisibleAnnotations'):
                for _ in range(attr.u2()): annotation(attr, depth+1)
            elif name in ('RuntimeVisibleParameterAnnotations','RuntimeInvisibleParameterAnnotations'):
                for _ in range(attr.u1()):
                    for _ in range(attr.u2()): annotation(attr, depth+1)
            elif name in ('RuntimeVisibleTypeAnnotations','RuntimeInvisibleTypeAnnotations'):
                for _ in range(attr.u2()):
                    target_type = attr.u1()
                    if target_type in (0x00,0x01,0x16): attr.u1()
                    elif target_type in (0x10,0x17,0x42,0x43,0x44,0x45,0x46): attr.u2()
                    elif target_type in (0x11,0x12): attr.u1(); attr.u1()
                    elif target_type in (0x13,0x14,0x15): pass
                    elif target_type in (0x40,0x41): attr.take(attr.u2()*6)
                    elif 0x47 <= target_type <= 0x4B: attr.u2(); attr.u1()
                    else: raise ClassFormatError("Java type annotation hedefi geçersiz")
                    for _ in range(attr.u1()):
                        kind, argument = attr.u1(), attr.u1()
                        if kind > 3 or (kind != 3 and argument != 0):
                            raise ClassFormatError("Java type annotation yolu geçersiz")
                    annotation(attr, depth+1)
            elif name == 'Module':
                ref(attr.u2(), (19,)); attr.u2(); ref(attr.u2(), (1,), protect=True, zero=True)
                for _ in range(attr.u2()):
                    ref(attr.u2(), (19,)); attr.u2(); ref(attr.u2(), (1,), protect=True, zero=True)
                for _kind in ('exports','opens'):
                    for _ in range(attr.u2()):
                        ref(attr.u2(), (20,)); attr.u2()
                        for _ in range(attr.u2()): ref(attr.u2(), (19,))
                for _ in range(attr.u2()): ref(attr.u2(), (7,))
                for _ in range(attr.u2()):
                    ref(attr.u2(), (7,))
                    for _ in range(attr.u2()): ref(attr.u2(), (7,))
            elif name == 'AnnotationDefault': element(attr, depth+1)
            elif name == 'BootstrapMethods':
                for _ in range(attr.u2()):
                    ref(attr.u2(), (15,))
                    for _ in range(attr.u2()): ref(attr.u2(), (3,4,5,6,7,8,15,16,17))
            elif name == 'Record':
                for _ in range(attr.u2()):
                    ref(attr.u2(), (1,), protect=True); ref(attr.u2(), (1,), protect=True)
                    attributes(attr, depth+1)
            elif name in ('StackMapTable','LineNumberTable'):
                # These standard attributes contain no direct Utf8 references;
                # preserve every byte, including bytecode offsets, unchanged.
                attr.take(len(attr.data))
            else:
                # Includes SourceDebugExtension: its text lives outside the CP.
                # Never silently skip custom payloads that may contain secrets.
                # Oznitelik adi mesaja DAHIL - aksi halde hangi class dosyasinin
                # neden reddedildigi rapordan/denetim kaydindan ANLASILAMAZ,
                # sadece jenerik "bilinmiyor" gorunurdu (bkz. gercek intranet
                # calismasinda "bazi .class dosyalari disarida kaliyor" sorusu -
                # bu isim olmadan kullanicinin HANGI oznitelik oldugunu bulmasi
                # imkansizdi).
                raise ClassFormatError(
                    f"Java özniteliği ({name}) henüz güvenli tarama kapsamında değil; çıktı engellendi"
                )
            attr.done()

    r.u2()  # access_flags
    ref(r.u2(), (7,)); ref(r.u2(), (7,), zero=True)
    for _ in range(r.u2()): ref(r.u2(), (7,))
    for _kind in ('fields', 'methods'):
        for _ in range(r.u2()):
            r.u2()
            member_name = ref(r.u2(), (1,), protect=True)
            ref(r.u2(), (1,), protect=True)
            attributes(r, field_name=member_name if _kind == 'fields' else None)
    attributes(r)
    r.done()
    return JavaClass(raw, tuple(strings), frozenset(editable - protected),
                     tuple(field_labels.get(entry[0], 'value') for entry in strings))
