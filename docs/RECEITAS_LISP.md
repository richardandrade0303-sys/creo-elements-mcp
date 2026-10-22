# Receitas Lisp verificadas

Receitas para usar com `creo_execute_lisp` (entrada fixa) e `creo_run_command` (entrada variável, texto da linha de comando).
Todas foram verificadas ao vivo no Creo Elements/Direct Modeling Express 9.0 (Lisp "HCL" 1.0 90.0P1017), **exceto** onde está dito o contrário.
Antes de usar qualquer nome que você não viu funcionar, rode `creo_lisp_apropos`. Nunca introspecte funções OLI
(estouro de pilha fatal: veja [`LICOES.md`](LICOES.md)).

## Modelo de execução segura
`ignore-errors` devolve `NIL` + a condição em vez de abrir diálogo:
```lisp
(let ((r (multiple-value-list
           (ignore-errors (progn <FORMAS>)))))
  (if (and (null (first r)) (second r))
      (list :erro (format nil "~A" (second r)))
      (list :ok (first r))))
```
Se `dialogs` vier não vazio mesmo assim, o erro veio de dentro de uma Action Routine (camada C++): leia o texto, corrija os parâmetros e repita.
Dentro de diálogos, o equivalente é `sd-call-cmds ... :failure`.

## Consultas só de leitura
```lisp
(oli:sd-inq-curr-part)                          ; peça atual (pode não existir)
(oli:sd-inq-curr-wp)                            ; workplane atual
(oli:sd-pathname-to-obj "/p1")                  ; objeto ou NIL
(oli:sd-inq-obj-pathname obj)                   ; "/p1"
(oli:sd-inq-part-p obj) (oli:sd-inq-assembly-p obj)
(oli:sd-inq-exact-box obj)                      ; bbox exato (é o que creo_get_part_info usa)
(oli:sd-inq-wp-origin wp) (oli:sd-inq-wp-w-dir wp)
(oli:sd-gen-obj-basename :workplane)            ; nome livre para um novo objeto
(oli:sd-license-free-module-p "Annotation")     ; NIL no Express
```
Evite `sd-inq-obj-children` aplicado a uma **peça** (`:PART`): é suspeito de ter derrubado o Creo (não provado). Para peças, use `sd-inq-exact-box`.

## Comandos de entrada fixa (chamada direta em Lisp)
| Tarefa | Chamada |
|---|---|
| Retângulo / círculo | `(rectangle 0,0 50,30)` · `(circle :cen_rad 100,100 10)` |
| Polígono fechado | `(polygon 0,0 40,0 40,20 :close)` (sem vértices consecutivos iguais) |
| Extrusão | `(extrude :part "/p1" :wp "/w1" :distance 20 :keep_profile :no)`; para baixo: `:direction :-w` |
| Criar peça / workplane | `(create_part :name "nome")` · `(create_workplane :new :name "wz")` |
| Apagar | `(delete_3d "/nome")` |
| Workplane corrente | `(current_wp "/wz")`, depois `(current_wp "/w1")` |
| Booleanas | `unite_3d`, `subtract_3d`, `intersect_3d` (a ferramenta precisa tocar o bloco) |
| Montagem real | `(create_assembly :name "conjunto")` (sem barra) · `(change_pa_owner :new_owner "/conjunto" :parts_assemblies "/peca")` · `(create_part :name "p" :owner "/conjunto")` |
| Hélice | `(helix_dia [:remove_material] :blank_part "/p" :wp "/wz" :axis :two_pta 0,0,0 0,0,10 :pitch P :turns N)` (passo ≥ 1,02 × altura do perfil) |
| Cor RGB | `(funcall (find-symbol "SET_PART_COLOR" "ELAN") obj :rgb (oli:make-gpnt3d :x r :y g :z b) :done)` com r, g, b de 0 a 1; consulta: `oli:sd-inq-part-color` |

## Comandos de entrada variável (texto da linha de comando)
Use `creo_run_command` (ou `(oli:sd-put-buffer "...")`). São **assíncronos**: confira o efeito numa chamada seguinte.
```text
STL :FILE "C:/pasta/a.stl" :OVERWRITE :PARTS_ASSEMBLIES "/p1" :BINARY COMPLETE
POSITION_WP "/wz" :PT_PT_PT 0,0,30 1,0,30 0,1,30 COMPLETE
POSITION_PA "/p" :TRANSLATE 10,20,30 COMPLETE
POSITION_PA "/p" :ROTATE :AXIS :TWO_PTA 0,0,0 0,1,0 :ROTATION_ANGLE 90 COMPLETE
REMOVE_FROM_VP_DRAWLIST "vport1" "/peca.1" COMPLETE
```
- `STL` com vários nomes em `:PARTS_ASSEMBLIES` exporta **só o primeiro**: um arquivo por peça.
- A rotação segue a **regra da mão direita** e o ângulo vai em **graus** (+90° em Y leva z→x; +90° em X leva y→z; −90° em X leva z→y).
  Em um bloco de 10 mm, 45° dá um bbox de 14,142 mm.
- Planos de trabalho: z = (0,0,h) (1,0,h) (0,1,h); y = (0,h,0) (1,h,0) (0,h,1), normal −y; x = (h,0,0) (h,1,0) (h,0,1), normal +x.

## Diálogos de usuário (o molde para tudo que precisa de ação)
`get_selection`, `GET_VOL_PROP`, `SECTION`, `MIRROR_3D` e `GENERAL_SWEEP_DIA` só funcionam dentro de um `sd-defdialog`
**sem variável `:part`**, chamado por `(oli:sd-put-buffer "NOME COMPLETE")`:
```lisp
(oli:sd-defdialog 'mcp_exemplo
  :ok-action '(oli:sd-call-cmds <comando> :success :ok
                :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message))))
(oli:sd-put-buffer "MCP_EXEMPLO COMPLETE")   ; assíncrono
```
Passe a peça por variável global e `(oli:sd-pathname-to-obj *global*)`. Atenção: `:success :ok` **substitui** o valor devolvido por `:OK`
(use só em comandos cujo valor não interessa; para medir volume, veja abaixo).

### Chanfro e filete (arestas selecionadas)
Receita implementada em `recipes.edge_dialog_define()` (ferramenta `creo_edge_treatment`):
```lisp
(defvar *mcp-eo-part* "") (defvar *mcp-eo-mode* :chamfer) (defvar *mcp-eo-size* 2.0)
(defvar *mcp-eo-result* :unset)
(oli:sd-defdialog 'mcp_edge_op2            ; SEM :variables (:part usa a peça corrente!)
  :ok-action '(setf *mcp-eo-result*
     (let* ((part-item (oli:sd-pathname-to-obj *mcp-eo-part*))
            (edges (oli:sd-call-cmds (oli:get_selection :focus_type oli:*sd-edge-3d-seltype*
                                                        :select :selected_part part-item :all_3d)))
            (sel (remove-if-not <predicado sobre os extremos da aresta> edges)))
       (list (length edges) (length sel)
             (oli:sd-call-cmds
               (if (eq *mcp-eo-mode* :blend)
                   (oli:blend :const_radius sel :new_radius *mcp-eo-size*)
                   (oli:chamfer :distance sel :new_distance *mcp-eo-size*))
               :success :ok
               :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message)))))))
(oli:sd-put-buffer "MCP_EDGE_OP2 COMPLETE")   ; depois ler *mcp-eo-result* → (total selecionadas :OK)
```
- Sintaxe: `(oli:chamfer :distance <arestas> :new_distance d)`, `(oli:blend :const_radius <arestas> :new_radius r)`.
  Assimétrico: `:dist_dist` (`:new_distance1` / `:new_distance2`) e `:dist_angle` (`:new_angle_dist` + `:new_angle` em **radianos**).
- Geometria da aresta: `(oli:sd-inq-edge-geo e :dest-space :global)` → `oli:sd-edge-start-pnt` / `-end-pnt`; coordenadas `oli:gpnt3d_x/_y/_z`.
  Aresta circular fechada: início = fim.
- Contagens observadas: um bloco tem 12 arestas; depois do filete dos 4 cantos, 24 (8 no topo).

### Casca (`SHELL_3D`)
Mesmo molde (`MCP_SHELL_OP`): faces listadas com `oli:*sd-face-seltype*`, filtradas pela caixa da face (`sd-face-ll-pnt` / `-ur-pnt`, globais)
e `oli:shell_3d`. `:offset` positivo = parede para dentro. Bloco 60×40×30, parede 3, topo aberto: 22 428 mm³ exatos. Use `creo_shell`.

### Volume, área e massa exatos (`GET_VOL_PROP`)
Analítico, sem a tesselação do STL. Molde (adaptado do diálogo de arestas; a forma **sem** `:success`):
```lisp
(defvar *mcp-vol* :unset)
(oli:sd-defdialog 'mcp_vol
  :ok-action '(setf *mcp-vol*
     (oli:sd-call-cmds (oli:get_vol_prop :for_part (oli:sd-pathname-to-obj "/peca") :volume))))  ; ou :area, :mass
(oli:sd-put-buffer "MCP_VOL COMPLETE")   ; depois ler *mcp-vol*
```
Bloco de 1 000 mm³: volume 1 000,00 e área 600,00 exatos. **Nota honesta:** numa tentativa posterior com o molde `... :success :ok` o
resultado voltou só `:OK`; a forma acima (sem `:success`) é a que devolveu o valor, mas **não foi repetida** depois desse achado.

### Corte 3D (`SECTION`)
```lisp
(oli:sd-defdialog 'mcp_section_cut
  :ok-action '(oli:sd-call-cmds
                (oli:section :parts (oli:sd-pathname-to-obj "/peca") :type :plane
                             :sectionplane (oli:make-gpnt3d :x x0 :y y0 :z z0) (oli:make-gpnt3d :x nx :y ny :z nz))
                :success :ok
                :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message))))
(oli:sd-put-buffer "MCP_SECTION_CUT COMPLETE")
```
A peça vira duas metades; a nova ganha sufixo `.1` e fica no mesmo `:ASSEMBLY`. Ocultar a metade da frente: `REMOVE_FROM_VP_DRAWLIST "vport1" "/peca.1" COMPLETE`.

### Espelho 3D (`MIRROR_3D`)
```lisp
(oli:sd-defdialog 'mcp_mirror
  :ok-action '(oli:sd-call-cmds
                (oli:mirror_3d :parts (oli:sd-pathname-to-obj "/peca")
                               :mirror_plane :pt_dir (oli:make-gpnt3d :x x :y y :z z) (oli:make-gpnt3d :x nx :y ny :z nz))
                :success :ok :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message))))
(oli:sd-put-buffer "MCP_MIRROR COMPLETE")
```
Também `:two_pta <p1> <p2>` e os planos globais `:x` / `:y` / `:z`.

### Varredura 3D (`GENERAL_SWEEP_DIA`)
```lisp
(oli:sd-defdialog 'mcp_sweep
  :ok-action '(oli:sd-call-cmds
                (oli:general_sweep_dia :add_material :blank_part (oli:sd-pathname-to-obj "/peca")
                                       :wp (oli:sd-pathname-to-obj "/wz") :spine <aresta-3d> :control :const)
                :success :ok :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message))))
```
O workplane do perfil **não** pode ser paralelo à tangente da espinha (`Spine tangent at attachment is parallel to profile WP`). Verificado
com um perfil circular r = 3 varrido ao longo de uma aresta vertical de 50 mm (volume 125 695,08 mm³, exato). Espinha livre (curva/spline)
**não foi provada**.

## O que o Express 9.0 não tem
`REVOLVE`, ângulo de saída no `EXTRUDE`, o módulo Annotation (`(oli:sd-license-free-module-p "Annotation")` → `NIL`) e Sheet Metal (`UNFOLD`).
`ADD_LOFT` exige conjunto de planos e *matchlines* (interativo) e não foi provado. Veja o [limite de 60 peças](LIMITE_60_PECAS.md).
