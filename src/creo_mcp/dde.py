"""Cliente DDE (DDEML) em ctypes puro para o Creo Elements/Direct Modeling.

O Creo age como servidor DDE quando `(oli:sd-dde-enable :on)` foi executado:
  - servico: "PESD"   - topico: "GENERAL"
  - XTYP_EXECUTE  -> executa uma string Lisp
  - XTYP_REQUEST  -> devolve o valor (string) de uma variavel Lisp
Referencia: Integration Kit, reference/dde.html (strings em UTF-16).

Cada chamada abre e fecha a propria conversacao DDE na mesma thread
(a instancia DDEML pertence a thread que a criou).
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"

SERVICE = "PESD"
TOPIC = "GENERAL"

XTYP_EXECUTE = 0x4050
XTYP_REQUEST = 0x20B0
CF_TEXT = 1
CF_UNICODETEXT = 13
CP_WINUNICODE = 1200
APPCMD_CLIENTONLY = 0x10
CBF_SKIP_ALLNOTIFICATIONS = 0x3F000
DDE_FACK = 0x8000
DDE_FBUSY = 0x4000

DMLERR = {
    0x4000: "ADVACKTIMEOUT",
    0x4001: "BUSY (Creo ocupado)",
    0x4002: "DATAACKTIMEOUT (tempo esgotado esperando dados)",
    0x4003: "DLL_NOT_INITIALIZED",
    0x4004: "DLL_USAGE",
    0x4005: "EXECACKTIMEOUT (tempo esgotado esperando o Creo terminar o comando)",
    0x4006: "INVALIDPARAMETER",
    0x4007: "LOW_MEMORY",
    0x4008: "MEMORY_ERROR",
    0x4009: "NOTPROCESSED (o Creo recusou o comando)",
    0x400A: "NO_CONV_ESTABLISHED (servidor PESD/GENERAL nao encontrado)",
    0x400B: "POKEACKTIMEOUT",
    0x400C: "POSTMSG_FAILED",
    0x400D: "REENTRANCY",
    0x400E: "SERVER_DIED (o Creo fechou)",
    0x400F: "SYS_ERROR",
    0x4010: "UNADVACKTIMEOUT",
    0x4011: "UNFOUND_QUEUE_ID",
}


class DdeError(RuntimeError):
    """Falha de comunicacao DDE. `code` e o codigo DMLERR (ou 0)."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


def _describe(code: int) -> str:
    return DMLERR.get(code, f"DMLERR 0x{code:04X}")


if IS_WINDOWS:
    import ctypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    _CALLBACK = ctypes.WINFUNCTYPE(
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_size_t,
    )

    _u32.DdeInitializeW.argtypes = [ctypes.POINTER(ctypes.c_uint32), _CALLBACK, ctypes.c_uint32, ctypes.c_uint32]
    _u32.DdeInitializeW.restype = ctypes.c_uint
    _u32.DdeUninitialize.argtypes = [ctypes.c_uint32]
    _u32.DdeUninitialize.restype = ctypes.c_int
    _u32.DdeCreateStringHandleW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_int]
    _u32.DdeCreateStringHandleW.restype = ctypes.c_void_p
    _u32.DdeFreeStringHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    _u32.DdeFreeStringHandle.restype = ctypes.c_int
    _u32.DdeConnect.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    _u32.DdeConnect.restype = ctypes.c_void_p
    _u32.DdeDisconnect.argtypes = [ctypes.c_void_p]
    _u32.DdeDisconnect.restype = ctypes.c_int
    _u32.DdeClientTransaction.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    _u32.DdeClientTransaction.restype = ctypes.c_void_p
    _u32.DdeGetData.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32]
    _u32.DdeGetData.restype = ctypes.c_uint32
    _u32.DdeFreeDataHandle.argtypes = [ctypes.c_void_p]
    _u32.DdeFreeDataHandle.restype = ctypes.c_int
    _u32.DdeGetLastError.argtypes = [ctypes.c_uint32]
    _u32.DdeGetLastError.restype = ctypes.c_uint

    def _dummy_callback(*_args):  # clientes so precisam de um callback vazio
        return 0

    _CB = _CALLBACK(_dummy_callback)  # mantido vivo no modulo (evita GC)


class DdeClient:
    """Conversacao DDE sincrona com o Creo (use como context manager)."""

    def __init__(self, service: str = SERVICE, topic: str = TOPIC):
        self.service = service
        self.topic = topic
        self._inst = ctypes.c_uint32(0) if IS_WINDOWS else None
        self._hconv = None

    # -- ciclo de vida -------------------------------------------------
    def open(self) -> "DdeClient":
        if not IS_WINDOWS:
            raise DdeError("DDE so existe no Windows (o servidor MCP deve rodar no mesmo PC do Creo).")
        rc = _u32.DdeInitializeW(ctypes.byref(self._inst), _CB, APPCMD_CLIENTONLY | CBF_SKIP_ALLNOTIFICATIONS, 0)
        if rc != 0:
            raise DdeError(f"DdeInitialize falhou: {_describe(rc)}", rc)
        hsz_service = _u32.DdeCreateStringHandleW(self._inst.value, self.service, CP_WINUNICODE)
        hsz_topic = _u32.DdeCreateStringHandleW(self._inst.value, self.topic, CP_WINUNICODE)
        try:
            self._hconv = _u32.DdeConnect(self._inst.value, hsz_service, hsz_topic, None)
            if not self._hconv:
                code = _u32.DdeGetLastError(self._inst.value)
                self._shutdown()
                raise DdeError(
                    f"Nao foi possivel conectar em {self.service}/{self.topic}: {_describe(code)}. "
                    "O Creo esta aberto e com DDE ligado? (no Creo: (oli:sd-dde-enable :on))",
                    code,
                )
        finally:
            _u32.DdeFreeStringHandle(self._inst.value, hsz_service)
            _u32.DdeFreeStringHandle(self._inst.value, hsz_topic)
        return self

    def _shutdown(self) -> None:
        if IS_WINDOWS and self._inst is not None and self._inst.value:
            _u32.DdeUninitialize(self._inst.value)
            self._inst = ctypes.c_uint32(0)

    def close(self) -> None:
        if not IS_WINDOWS:
            return
        if self._hconv:
            _u32.DdeDisconnect(self._hconv)
            self._hconv = None
        self._shutdown()

    def __enter__(self) -> "DdeClient":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- transacoes ----------------------------------------------------
    def execute(self, command: str, timeout_ms: int = 30000) -> None:
        """Executa uma string Lisp no Creo (XTYP_EXECUTE)."""
        data = (command + "\0").encode("utf-16-le")
        status = ctypes.c_uint32(0)
        res = _u32.DdeClientTransaction(
            data, len(data), self._hconv, None, 0, XTYP_EXECUTE, timeout_ms, ctypes.byref(status)
        )
        if not res:
            code = _u32.DdeGetLastError(self._inst.value)
            raise DdeError(f"DDE Execute falhou: {_describe(code)}", code)
        if status.value & DDE_FBUSY:
            raise DdeError("Creo respondeu BUSY ao comando.", 0x4001)

    def request(self, item: str, timeout_ms: int = 10000) -> str:
        """Le o valor (string) de uma variavel Lisp (XTYP_REQUEST)."""
        hsz = _u32.DdeCreateStringHandleW(self._inst.value, item, CP_WINUNICODE)
        try:
            for fmt in (CF_UNICODETEXT, CF_TEXT):
                hdata = _u32.DdeClientTransaction(None, 0, self._hconv, hsz, fmt, XTYP_REQUEST, timeout_ms, None)
                if hdata:
                    try:
                        size = _u32.DdeGetData(hdata, None, 0, 0)
                        buf = ctypes.create_string_buffer(size or 1)
                        _u32.DdeGetData(hdata, buf, size, 0)
                    finally:
                        _u32.DdeFreeDataHandle(hdata)
                    raw = buf.raw[:size]
                    if fmt == CF_UNICODETEXT:
                        return raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
                    return raw.decode("cp1252", errors="replace").split("\0", 1)[0]
            code = _u32.DdeGetLastError(self._inst.value)
            raise DdeError(f"DDE Request '{item}' falhou: {_describe(code)}", code)
        finally:
            _u32.DdeFreeStringHandle(self._inst.value, hsz)


def ping(service: str = SERVICE, topic: str = TOPIC) -> tuple[bool, str]:
    """Tenta abrir e fechar uma conversacao. Retorna (ok, mensagem)."""
    try:
        with DdeClient(service, topic):
            return True, "conectado"
    except DdeError as exc:
        return False, str(exc)
