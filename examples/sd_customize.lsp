;;; Creo MCP: liga o servidor DDE do Creo (servico "PESD", topico "GENERAL").
;;; O servidor MCP externo (Python) envia comandos Lisp por DDE; nao ha ponte em Lisp.
;;; Arquivo carregado por (oli:sd-load-customization-file "sd_customize") no startup.
;;;
;;; Copie para:
;;;   %APPDATA%\PTC\Creo Elements Direct Modeling Express 9.0\90.0\sd_customize.lsp
;;; (se o arquivo ja existir, acrescente apenas a linha abaixo).
(oli:sd-dde-enable :on)
