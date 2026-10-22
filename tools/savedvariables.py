#!/usr/bin/env python3
"""Read a WoW SavedVariables file (the Lua the client writes on logout) into Python values.

The client writes a restricted subset of Lua: top-level `Name = value` assignments, tables
with `["key"] = value`, `[1] = value` or positional entries, `-- [n]` comments, strings,
numbers, booleans and nil. This parses that subset and nothing more; it never executes code.

Tables whose keys are exactly 1..n become lists, everything else becomes a dict.

  from savedvariables import load
  db = load("WTF/Account/NAME/SavedVariables/APISurface.lua")["APISurfaceDB"]
"""
from __future__ import annotations

import re
from pathlib import Path

_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34, "'": 39, "\n": 10}
_NUMBER = re.compile(r"0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class LuaSyntaxError(ValueError):
    pass


class _Parser:
    def __init__(self, src: str):
        self.s = src
        self.n = len(src)
        self.p = 0

    def error(self, msg: str) -> LuaSyntaxError:
        line = self.s.count("\n", 0, self.p) + 1
        return LuaSyntaxError(f"line {line}: {msg}")

    def skip(self) -> None:
        s, n = self.s, self.n
        while self.p < n:
            c = s[self.p]
            if c in " \t\r\n":
                self.p += 1
            elif s.startswith("--", self.p):
                self.p += 2
                m = re.compile(r"\[(=*)\[").match(s, self.p)
                if m:
                    end = s.find("]" + m.group(1) + "]", m.end())
                    self.p = n if end < 0 else end + len(m.group(1)) + 2
                else:
                    end = s.find("\n", self.p)
                    self.p = n if end < 0 else end + 1
            else:
                break

    def peek(self, tok: str) -> bool:
        self.skip()
        return self.s.startswith(tok, self.p)

    def expect(self, tok: str) -> None:
        if not self.peek(tok):
            raise self.error(f"expected {tok!r}")
        self.p += len(tok)

    def name(self) -> str:
        self.skip()
        m = _NAME.match(self.s, self.p)
        if not m:
            raise self.error("expected a name")
        self.p = m.end()
        return m.group(0)

    def string(self) -> str:
        s = self.s
        q = s[self.p]
        if q == "[":
            m = re.compile(r"\[(=*)\[").match(s, self.p)
            if not m:
                raise self.error("bad long string")
            close = "]" + m.group(1) + "]"
            end = s.find(close, m.end())
            if end < 0:
                raise self.error("unterminated long string")
            body = s[m.end():end]
            self.p = end + len(close)
            return body[1:] if body.startswith("\n") else body
        self.p += 1
        out = bytearray()
        while True:
            if self.p >= self.n:
                raise self.error("unterminated string")
            c = s[self.p]
            if c == q:
                self.p += 1
                return out.decode("utf-8", errors="surrogateescape")
            if c == "\\":
                self.p += 1
                e = s[self.p]
                if e in _ESCAPES:
                    out.append(_ESCAPES[e])
                    self.p += 1
                elif e == "x":
                    out.append(int(s[self.p + 1:self.p + 3], 16))
                    self.p += 3
                elif e == "z":
                    self.p += 1
                    while self.p < self.n and s[self.p] in " \t\r\n":
                        self.p += 1
                elif e.isdigit():
                    m = re.compile(r"\d{1,3}").match(s, self.p)
                    out.append(int(m.group(0)))
                    self.p = m.end()
                elif e == "u":
                    end = s.index("}", self.p)
                    out += chr(int(s[self.p + 2:end], 16)).encode("utf-8")
                    self.p = end + 1
                else:
                    raise self.error(f"bad escape \\{e}")
            else:
                out += c.encode("utf-8", errors="surrogateescape")
                self.p += 1

    def value(self):
        self.skip()
        s = self.s
        if self.p >= self.n:
            raise self.error("unexpected end of file")
        c = s[self.p]
        if c in "\"'" or s.startswith("[[", self.p) or s.startswith("[=", self.p):
            return self.string()
        if c == "{":
            return self.table()
        if c == "-":
            self.p += 1
            v = self.value()
            if not isinstance(v, (int, float)):
                raise self.error("unary minus on a non-number")
            return -v
        m = _NUMBER.match(s, self.p)
        if m:
            self.p = m.end()
            t = m.group(0)
            if t[:2] in ("0x", "0X"):
                return int(t, 16)
            return float(t) if any(ch in t for ch in ".eE") else int(t)
        word = self.name()
        if word == "true":
            return True
        if word == "false":
            return False
        if word == "nil":
            return None
        if word in ("inf", "nan"):  # some clients write these for 1/0 and 0/0
            return float(word)
        raise self.error(f"unexpected {word!r}")

    def table(self):
        self.expect("{")
        d: dict = {}
        pos = 0
        while not self.peek("}"):
            if self.peek("["):
                if self.s.startswith("[[", self.p) or self.s.startswith("[=", self.p):
                    pos += 1
                    d[pos] = self.value()
                else:
                    self.p += 1
                    key = self.value()
                    self.expect("]")
                    self.expect("=")
                    d[key] = self.value()
            else:
                m = _NAME.match(self.s, self.p)
                after = m and re.compile(r"\s*=(?!=)").match(self.s, m.end())
                if m and after and m.group(0) not in ("true", "false", "nil"):
                    self.p = after.end()
                    d[m.group(0)] = self.value()
                else:
                    pos += 1
                    d[pos] = self.value()
            if self.peek(",") or self.peek(";"):
                self.p += 1
            elif not self.peek("}"):
                raise self.error("expected ',' or '}'")
        self.p += 1
        d = {k: v for k, v in d.items() if v is not None}
        if d and all(isinstance(k, int) and not isinstance(k, bool) for k in d) and sorted(d) == list(range(1, len(d) + 1)):
            return [d[k] for k in range(1, len(d) + 1)]
        return d if d else {}

    def chunk(self) -> dict:
        out = {}
        while True:
            self.skip()
            if self.p >= self.n:
                return out
            key = self.name()
            self.expect("=")
            out[key] = self.value()


def loads(src: str) -> dict:
    """Parse SavedVariables source text into {global name: value}."""
    if src.startswith("﻿"):
        src = src[1:]
    return _Parser(src).chunk()


def load(path: str | Path) -> dict:
    return loads(Path(path).read_text(encoding="utf-8", errors="surrogateescape"))
