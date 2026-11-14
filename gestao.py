import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

def exigir_edicao_local():
    if os.getenv("VERCEL"):
        raise HTTPException(403, "A demonstração pública permite apenas consultas e planejamento.")

def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

def migrate():
    with connect() as db:
        db.execute(Path(__file__).with_name("migration.sql").read_text(encoding="utf-8-sig"))

class Produto(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    custo: Decimal = Field(ge=0)
    preco: Decimal = Field(ge=0)

class Fornecedor(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    cnpj: str = Field(pattern=r"^[0-9]{14}$")
    endereco: str = Field(min_length=1)
    contato: str = ""

class Oferta(BaseModel):
    produto: int
    fornecedor: str
    preco: Decimal = Field(gt=0)
    prazo: int = Field(ge=0, le=365)
    minimo: int = Field(gt=0)

class Entrada(BaseModel):
    produto: int
    fornecedor: str
    cofre: int
    entrada: date
    validade: date
    quantidade: int = Field(gt=0)
    custo: Decimal = Field(ge=0)

class Venda(BaseModel):
    lote: int
    data: date
    quantidade: int = Field(gt=0)
    preco: Decimal = Field(ge=0)

def write(sql, values):
    try:
        with connect() as db:
            db.execute(sql, values)
    except psycopg.IntegrityError:
        raise HTTPException(409, "Verifique os dados. Há um cadastro duplicado ou uma referência inválida.")
    return {"ok": True}

@router.post("/produtos", dependencies=[Depends(exigir_edicao_local)])
def produto(p: Produto):
    return write("INSERT INTO produtos(nome,custo,preco_venda) VALUES (%s,%s,%s)", (p.nome,p.custo,p.preco))

@router.put("/produtos/{id}", dependencies=[Depends(exigir_edicao_local)])
def editar(id: int,p: Produto):
    return write("UPDATE produtos SET nome=%s,custo=%s,preco_venda=%s WHERE id=%s",(p.nome,p.custo,p.preco,id))

@router.post("/fornecedores", dependencies=[Depends(exigir_edicao_local)])
def fornecedor(f: Fornecedor):
    return write("""INSERT INTO fornecedores VALUES (%s,%s,%s,%s) ON CONFLICT(nome) DO UPDATE
        SET cnpj=excluded.cnpj,endereco=excluded.endereco,contato=excluded.contato""",(f.nome,f.cnpj,f.endereco,f.contato))

@router.post("/ofertas", dependencies=[Depends(exigir_edicao_local)])
def oferta(o: Oferta):
    with connect() as db:
        if not db.execute("SELECT 1 FROM fornecedores WHERE nome=%s",(o.fornecedor,)).fetchone():
            raise HTTPException(422,"Fornecedor desconhecido.")
    return write("""INSERT INTO precos SELECT %s,%s,dia,%s,%s,%s FROM cenario
        ON CONFLICT(produto,fornecedor,data) DO UPDATE SET preco=excluded.preco,minimo=excluded.minimo,prazo=excluded.prazo""",
        (o.produto,o.fornecedor,o.preco,o.minimo,o.prazo))

@router.post("/entradas", dependencies=[Depends(exigir_edicao_local)])
def entrada(e: Entrada):
    with connect() as db:
        dia=db.execute("SELECT dia FROM cenario").fetchone()["dia"]
    if e.entrada>dia or e.validade<e.entrada:
        raise HTTPException(422,"Confira a entrada, a validade e a data do cenário.")
    return write("""INSERT INTO lotes(produto,fornecedor,cofre,entrada,validade,inicial,custo)
        VALUES (%s,%s,%s,%s,%s,%s,%s)""",(e.produto,e.fornecedor,e.cofre,e.entrada,e.validade,e.quantidade,e.custo))

@router.post("/vendas", dependencies=[Depends(exigir_edicao_local)])
def venda(v: Venda):
    with connect() as db:
        db.execute("SELECT id FROM lotes WHERE id=%s FOR UPDATE",(v.lote,))
        l=db.execute("SELECT s.*,l.entrada,dia FROM saldo s JOIN lotes l USING(id) CROSS JOIN cenario WHERE s.id=%s",(v.lote,)).fetchone()
        if not l or v.quantidade>l["unidades"]:
            raise HTTPException(409,"Saldo insuficiente nesse lote.")
        if v.data>min(l["dia"],l["validade"]) or (l["entrada"] and v.data<l["entrada"]):
            raise HTTPException(422,"A venda precisa estar entre a entrada e a validade, até a data do cenário.")
        db.execute("INSERT INTO retiradas(lote,instante,usuario,qtd,preco_venda) VALUES (%s,%s::date+time '12:00','Venda',%s,%s)",(v.lote,v.data,v.quantidade,v.preco))
    return {"ok": True}

@router.get("/catalogo")
def catalogo():
    queries = {
        "produtos":"SELECT * FROM produtos ORDER BY nome",
        "fornecedores":"SELECT * FROM fornecedores ORDER BY nome",
        "cofres":"SELECT * FROM cofres ORDER BY nome",
        "ofertas":"SELECT DISTINCT ON(produto,fornecedor) * FROM precos ORDER BY produto,fornecedor,data DESC",
        "lotes":"SELECT s.*,l.entrada,l.fornecedor FROM saldo s JOIN lotes l USING(id) ORDER BY validade,id",
        "movimentos":"SELECT lote,instante,qtd,usuario,preco_venda FROM retiradas ORDER BY instante DESC,lote",
    }
    with connect() as db:
        if os.getenv("VERCEL"):
            queries["fornecedores"] = "SELECT nome FROM fornecedores ORDER BY nome"
            queries["movimentos"] = "SELECT lote,instante,qtd,preco_venda FROM retiradas ORDER BY instante DESC,lote"
        return dict(edicao_local=not os.getenv("VERCEL"), **{key:db.execute(sql).fetchall() for key,sql in queries.items()})
