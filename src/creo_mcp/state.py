"""Estado de processo que NAO pode ser recarregado a quente.

A recarga a quente (server._reload) re-executa dde/winui/lisp/recipes/...; qualquer trava criada dentro desses modulos
seria substituida por uma nova no meio de um job. Por isso a trava da conversacao DDE mora aqui, e este modulo nunca e
recarregado.
"""

from __future__ import annotations

import threading
import uuid

DDE_LOCK = threading.Lock()          # uma conversacao DDE por vez, dentro deste processo
SESSION_ID = uuid.uuid4().hex[:8]    # identifica esta execucao do servidor no diario e nos incidentes
