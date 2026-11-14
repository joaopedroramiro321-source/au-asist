import re
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import hashlib
import os
import secrets
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from psycopg.types.json import Jsonb

from cenario import ler_cenario
from gestao import connect
from reposicao import cobertura, moeda


class Acao(BaseModel):
    tipo: Literal["comparar", "preparar", "confirmar", "cancelar", "analisar", "validade"]
    produto: int | None = Field(None, gt=0)
    proposta: UUID | None = None
    versao: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def validar_referencia(self):
        if self.tipo in ("confirmar","cancelar") and (not self.proposta or not self.versao):
            raise ValueError("A ação precisa identificar a proposta e a versão revisada.")
        if self.tipo == "analisar" and not self.produto:
            raise ValueError("Selecione um produto para analisar.")
        return self


class Entrada(BaseModel):
    pergunta: str = Field(min_length=1, max_length=500)
    anterior: str = Field("", max_length=500)
    conversa_id: UUID | None = None
    acao: Acao | None = None


class Cobertura(BaseModel):
    tipo: Literal["cobertura"] = "cobertura"
    nome: str
    saldo: int
    dias: Decimal | None


class Validade(BaseModel):
    tipo: Literal["validade"] = "validade"
    nome: str
    lote: int
    validade: str
    unidades: int
    perda: Decimal


class Oferta(BaseModel):
    nome: str
    fornecedor: str
    prazo: int
    minimo: int
    unitario: Decimal
    quantidade: int
    total: Decimal
    atende: bool


class Comparacao(Oferta):
    tipo: Literal["comparacao"] = "comparacao"


class Resumo(BaseModel):
    tipo: Literal["proposta"] = "proposta"
    itens: list[Oferta]
    total: Decimal
    status: str
    id: UUID | None = None
    versao: int | None = None


class Pendencia(BaseModel):
    tipo: Literal["pendencia"] = "pendencia"
    nome: str
    motivo: str


class Sugestao(BaseModel):
    chave: str
    motivo: str
    acao: Acao


class Resposta(BaseModel):
    resposta: str
    origem: str = "Dados do sistema"
    consulta: str
    conversa_id: UUID
    contexto: dict
    cartoes: list[Cobertura | Validade | Comparacao | Resumo | Pendencia] = []
    acoes: list[Acao] = []
    sugestoes: list[Sugestao] = []


class Esclarecer(ValueError):
    pass


class ApresentacaoAmbigua(Esclarecer):
    pass


def interpretar_parametros(q, estado, dados, norm, acao):
    alteracoes = {}
    produtos = dados["produtos"]
    if acao and acao.produto:
        if not any(p["id"] == acao.produto for p in produtos):
            raise Esclarecer("Esse produto não está no catálogo.")
        alteracoes["produtos"] = [acao.produto]
    else:
        nomes = [(p["id"], norm(p["nome"])) for p in produtos]
        busca = q.replace(".", ",")
        exatos = [i for i,n in nomes if n in busca]
        parciais = [(i,n) for i,n in nomes if n.split()[0] in busca]
        if exatos:
            alteracoes["produtos"] = exatos
        elif len(parciais) > 1:
            raise ApresentacaoAmbigua("Qual apresentação? " + " ou ".join(n for _,n in parciais))
        elif parciais:
            alteracoes["produtos"] = [parciais[0][0]]
        elif "todos os produtos" in q:
            alteracoes["produtos"] = []
    if re.search(r"sem (limite|orcamento)", q):
        alteracoes["orcamento"] = None
    elif "r$" in q or "orcamento" in q or "reais" in q:
        m = re.search(r"(?:r\$\s*|orcamento\s*(?:de\s*)?)(-?[\d.,]+)|(-?[\d.,]+)\s*reais",q)
        valor = (m.group(1) or m.group(2)).rstrip(".,") if m else ""
        if not re.fullmatch(r"(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d{1,2})?|\d+\.\d{1,2}",valor):
            raise Esclarecer("Informe um orçamento em reais, por exemplo R$ 500,00.")
        if "," in valor or re.search(r"\.\d{3}$",valor):
            valor = valor.replace(".","").replace(",",".")
        if not 0 < Decimal(valor) <= Decimal("999999999999999.99"):
            raise Esclarecer("Informe um orçamento positivo com até 15 dígitos inteiros.")
        alteracoes["orcamento"] = str(moeda(valor))
    dias = re.search(r"(-?[\d.,]+)\s*dias?\b",q)
    if dias:
        if not dias[1].isdigit() or not 1 <= int(dias[1]) <= 365:
            raise Esclarecer("Escolha um horizonte inteiro de 1 a 365 dias após a entrega.")
        alteracoes["horizonte_dias"] = int(dias[1])
    elif "horizonte" in q:
        raise Esclarecer("Informe o horizonte em dias, por exemplo: para 15 dias.")
    preferencia = re.fullmatch(r"(?:o )?fornecedor .+",q) or re.search(r"prefiro|preferencia|prioriz",q)
    if "sem preferencia" in q:
        alteracoes["fornecedor"] = None
    elif preferencia:
        candidatos = [n for n in dados["fornecedores"] if norm(n) in q]
        if len(candidatos) != 1:
            raise Esclarecer("Informe o nome cadastrado de um fornecedor.")
        alteracoes["fornecedor"] = candidatos[0]
    estado.update(alteracoes)
    return bool(alteracoes.keys() - {"produtos"})


def serializar(obj):
    from fastapi.encoders import jsonable_encoder
    from datetime import date
    return jsonable_encoder(obj, custom_encoder={Decimal:str, date:date.isoformat})


def revisar(db, cid, estado, plano, operacao, acao):
    referencia = estado.get("proposta")
    row = db.execute("SELECT * FROM propostas WHERE id=%s AND conversa=%s",
                     (referencia,cid)).fetchone() if referencia else None
    if operacao in ("confirmar","cancelar"):
        if not row or not acao or str(acao.proposta) != str(row["id"]) or acao.versao != row["versao"]:
            return "Selecione a ação da versão mais recente da proposta.", row
        if row["status"] != "rascunho":
            return f"Proposta {row['id']} {row['status']}.", row
        if operacao == "cancelar":
            db.execute("UPDATE propostas SET status='cancelada' WHERE id=%s",(row["id"],))
            return "Proposta cancelada.", dict(row,status="cancelada")
    parametros = {k:estado[k] for k in ("produtos","horizonte_dias","orcamento","fornecedor")}
    ids = parametros["produtos"]
    pertinentes = lambda p: not ids or p in ids
    revisao = serializar(dict(dia=plano["dia"], parametros=parametros, itens=plano["compras"],
        saldos=[(p["id"],p["estoque"],p["media"],p["desvio"]) for p in plano["produtos"] if pertinentes(p["id"])],
        ofertas=[o for o in plano["ofertas"] if pertinentes(o["produto"])], pendencias=plano["pendencias"]))
    mudou = not row or row["revisao"] != revisao
    if operacao == "confirmar" and not mudou and plano["compras"]:
        db.execute("UPDATE propostas SET status='confirmada' WHERE id=%s",(row["id"],))
        return f"Planejamento interno confirmado. Identificador {row['id']}.", dict(row,status="confirmada")
    if not row or row["status"] != "rascunho":
        if not plano["compras"]:
            return "Sem itens para preparar. Ajuste os parâmetros.", None
        id_, versao = uuid4(), 1
        db.execute("INSERT INTO propostas VALUES (%s,%s,'rascunho',%s,%s,%s,%s)",
                   (id_,cid,versao,plano["dia"],Jsonb(parametros),Jsonb(revisao)))
    else:
        id_, versao = row["id"], row["versao"] + int(mudou)
        if mudou:
            db.execute("UPDATE propostas SET versao=%s,dia=%s,parametros=%s,revisao=%s WHERE id=%s",
                       (versao,plano["dia"],Jsonb(parametros),Jsonb(revisao),id_))
            db.execute("DELETE FROM proposta_itens WHERE proposta=%s",(id_,))
    if mudou or not row or row["status"] != "rascunho":
        for i in plano["compras"]:
            db.execute("INSERT INTO proposta_itens VALUES (%s,%s,%s,%s,%s,%s)",
                       (id_,i["produto"],i["fornecedor"],i["quantidade"],i["unitario"],i["total"]))
    estado["proposta"] = str(id_)
    msg = "Dados alterados. Revise a nova versão." if operacao=="confirmar" else "Revise os itens e o total antes de confirmar."
    return msg, dict(id=id_,versao=versao,status="rascunho",revisao=revisao)


def dono_da_conversa(request, response):
    if not os.getenv("VERCEL"):
        return None
    cookie = request.cookies.get("__Host-au-sessao")
    if not cookie or not re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie):
        cookie = secrets.token_urlsafe(32)
        response.set_cookie("__Host-au-sessao", cookie, secure=True, httponly=True, samesite="strict")
    return hashlib.sha256(cookie.encode()).hexdigest()


def registrar_chat(app, montar_painel, consultar, norm):
    @app.get("/conversas/{cid}")
    def historico(cid: UUID, request: Request, response: Response):
        response.headers['Cache-Control'] = 'private, no-store'
        dono = dono_da_conversa(request, response)
        with connect() as db:
            if not db.execute("SELECT 1 FROM conversas WHERE id=%s AND dono IS NOT DISTINCT FROM %s",(cid,dono)).fetchone():
                raise HTTPException(404,"Conversa não encontrada.")
            return db.execute("SELECT pergunta,resposta FROM mensagens WHERE conversa=%s ORDER BY id",(cid,)).fetchall()

    @app.post("/chat",response_model=Resposta)
    def chat(entrada: Entrada, request: Request = None, response: Response = None):
        dono = dono_da_conversa(request, response)
        pergunta_original = entrada.pergunta
        cid = entrada.conversa_id or uuid4()
        with connect() as db:
            if not entrada.conversa_id:
                db.execute("INSERT INTO conversas(id,dono) VALUES (%s,%s)",(cid,dono))
            row = db.execute("SELECT estado FROM conversas WHERE id=%s AND dono IS NOT DISTINCT FROM %s FOR UPDATE",(cid,dono)).fetchone()
            if not row:
                raise HTTPException(404,"Conversa não encontrada.")
            estado = dict(produtos=[],horizonte_dias=None,orcamento=None,fornecedor=None,
                          intencao=None,pendencia=None)
            estado.update(row["estado"])
            # Um escritor operacional não pode alterar a base entre revisão e confirmação.
            if not os.getenv("VERCEL") and entrada.acao and entrada.acao.tipo == "confirmar":
                db.execute("LOCK TABLE cenario, produtos, lotes, retiradas, precos, fornecedores IN SHARE MODE")
            dados = ler_cenario(db)
            dados["fornecedores"] = [r["nome"] for r in db.execute("SELECT nome FROM fornecedores")]
            anterior = db.execute("SELECT pergunta FROM mensagens WHERE conversa=%s ORDER BY id DESC LIMIT 1",(cid,)).fetchone()
            if anterior:
                entrada.anterior = anterior["pergunta"]
            resposta = Resposta(consulta="esclarecimento",resposta="",conversa_id=cid,contexto=estado)
            try:
                q = norm(entrada.pergunta.strip().lower())
                acao = entrada.acao
                consulta_pendente = estado.pop("consulta_pendente", None)
                if consulta_pendente and not acao and any(norm(p["nome"]) in q.replace(".",",") for p in dados["produtos"]):
                    q = consulta_pendente + " " + q
                    entrada = entrada.model_copy(update={"pergunta":q})
                parametros = interpretar_parametros(q,estado,dados,norm,acao)
                pendente = estado.get("pendencia")
                estado["pendencia"] = None
                plano = montar_painel(dados, produtos_ids=estado["produtos"], horizonte=estado["horizonte_dias"],
                    orcamento=Decimal(estado["orcamento"]) if estado["orcamento"] else None, fornecedor=estado["fornecedor"])
                operacao = acao.tipo if acao else None
                if not operacao and re.search(r"prepar|rascunho|montar proposta",q):
                    operacao = "preparar"
                if not operacao and q in ("confirmar","confirmar proposta","cancelar proposta"):
                    operacao = "revisar"
                if parametros or operacao in ("preparar","confirmar","cancelar","revisar"):
                    intent = "compras"
                elif operacao:
                    intent = {"analisar":"cobertura","comparar":"fornecedores","validade":"validade"}[operacao]
                elif not consulta_pendente and (pendente or re.match(r"^e (a|o) ",q)) and estado["produtos"] and estado["intencao"]:
                    intent = estado["intencao"]
                elif re.search(r"comprar|repor|reposicao",q) and not re.search(r"fornecedor|sem rep|autonomia|dura",q):
                    intent = "compras"
                elif q in ("estoque", "meu estoque", "consultar estoque", "saldo do estoque"):
                    intent = "cobertura"
                else:
                    legado = consultar(entrada,plano)
                    intent = legado["consulta"]
                    resposta.resposta, resposta.origem = legado["resposta"], legado["origem"]
                resposta.consulta = estado["intencao"] = intent
                produtos = [p for p in plano["produtos"] if not estado["produtos"] or p["id"] in estado["produtos"]]
                if intent=="compras":
                    itens = plano["compras"]
                    resposta.resposta = ("Reposição sugerida para " + ", ".join(i["nome"] for i in itens) + "."
                                         if itens else "Nenhuma compra sugerida nas condições atuais.")
                    proposta = None
                    if operacao in ("preparar","confirmar","cancelar","revisar") or parametros and estado.get("proposta"):
                        op = operacao or "preparar"
                        resposta.resposta, proposta = revisar(db,cid,estado,plano,op,acao)
                    if proposta:
                        itens = proposta["revisao"]["itens"]
                    resumo = Resumo(itens=itens,total=sum(Decimal(str(i["total"])) for i in itens),
                                    status=proposta["status"] if proposta else "simulação",
                                    id=proposta["id"] if proposta else None,versao=proposta["versao"] if proposta else None)
                    resposta.cartoes.append(resumo)
                    if not proposta:
                        for i in itens:
                            p = next(p for p in produtos if p["id"] == i["produto"])
                            resposta.cartoes.append(Cobertura(nome=p["nome"],saldo=p["estoque"],dias=moeda(cobertura(p))))
                    resposta.cartoes.extend(Pendencia(**p) for p in plano["pendencias"])
                    resposta.acoes.append(Acao(tipo="comparar"))
                    if resumo.status=="rascunho":
                        for tipo in (["confirmar","cancelar"] if itens else ["cancelar"]):
                            resposta.acoes.append(Acao(tipo=tipo,proposta=resumo.id,versao=resumo.versao))
                    elif itens:
                        resposta.acoes.append(Acao(tipo="preparar"))
                    resposta.resposta += " A projeção usa o saldo válido hoje e pode superestimar a cobertura se houver vencimentos."
                elif intent=="cobertura":
                    for p in produtos:
                        dias = cobertura(p)
                        resposta.cartoes.append(Cobertura(nome=p["nome"],saldo=p["estoque"],dias=moeda(dias) if dias is not None else None))
                    resposta.resposta = "Cobertura estimada pelo consumo dos últimos 28 dias. " + ", ".join(p["nome"] + " • " + (f"{cobertura(p):.1f} dias".replace(".",",") if p["media"] else "sem consumo") for p in produtos)
                    resposta.resposta += ". Lotes que vencem antes do uso podem reduzir essa cobertura."
                    resposta.acoes.append(Acao(tipo="comparar"))
                elif intent=="fornecedores":
                    resposta.cartoes.extend(Comparacao(**o) for p in produtos for o in plano["comparacoes"][p["nome"]])
                    resposta.resposta = "Compare preço, mínimo e prazo de entrega." if resposta.cartoes else "Nenhuma comparação disponível."
                    resposta.acoes.append(Acao(tipo="preparar"))
                elif intent=="validade":
                    riscos = [r for r in plano["riscos"] if not estado["produtos"] or r["produto"] in estado["produtos"]]
                    resposta.cartoes.extend(Validade(**dict(r,validade=str(r["validade"]))) for r in riscos)
                    resposta.resposta = "Lotes em risco: " + ", ".join(r["nome"] for r in riscos) if riscos else "Nenhuma ocorrência no cenário."
                for p in produtos:
                    ofertas = plano["comparacoes"].get(p["nome"],[])
                    if ofertas and all(not o["atende"] for o in ofertas):
                        resposta.sugestoes.append(Sugestao(chave=f"ruptura-{p['id']}",motivo=p["nome"]+" pode acabar antes da entrega.",acao=Acao(tipo="comparar",produto=p["id"])))
                        break
                if plano["riscos"]:
                    r = plano["riscos"][0]
                    resposta.sugestoes.append(Sugestao(chave=f"lote-{r['lote']}",motivo=f"Lote {r['lote']} de {r['nome']} tem unidades em risco de vencer.",acao=Acao(tipo="validade",produto=r["produto"])))
                if intent == "compras":
                    falta = next((p for p in plano["pendencias"] if "orçamento" in p["motivo"] or "Faltam" in p["motivo"]),None)
                    if falta:
                        resposta.sugestoes.insert(0,Sugestao(chave=f"orcamento-{falta['produto']}",
                            motivo=f"{falta['nome']}: {falta['motivo']} Compare as ofertas.",
                            acao=Acao(tipo="comparar",produto=falta["produto"])))
                resposta.sugestoes = resposta.sugestoes[:2]
            except Esclarecer as e:
                estado["pendencia"] = resposta.resposta = str(e)
                if isinstance(e, ApresentacaoAmbigua):
                    estado["consulta_pendente"] = q
                if not estado["intencao"]:
                    estado["intencao"] = "fornecedores" if "compar" in q else "compras" if "compr" in q else "cobertura"
            resposta.contexto = estado
            db.execute("UPDATE conversas SET estado=%s WHERE id=%s",(Jsonb(estado),cid))
            db.execute("INSERT INTO mensagens(conversa,pergunta,resposta) VALUES (%s,%s,%s)",
                       (cid,pergunta_original,Jsonb(resposta.model_dump(mode="json"))))
            return resposta
