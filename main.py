from typing import Literal
import json
import math
import os
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(title="AU Asist • Assistente de compras")
app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")
from gestao import router, migrate, catalogo
app.include_router(router)
if not os.getenv("VERCEL"):
    app.add_event_handler("startup", migrate)
QUESTIONS = ["O que preciso comprar hoje?", "Quais lotes estão em risco de vencer?",
             "Quanto dinheiro está parado no estoque?", "Vale comprar semaglutida 1 mg do fornecedor B?",
             "Aconteceu algo estranho nos cofres esta semana?"]
INTENTS = ["compras", "validade", "capital", "fornecedores", "alertas"]
MONEY = lambda n: ("R$ {:,.2f}".format(n)).replace(",", "_").replace(".", ",").replace("_", ".")


from cenario import ler_cenario
from gestao import connect
from reposicao import simular, cobertura
from formas_assistente import FORMAS, CANONICA


def montar_painel(dados, **parametros):
    plano = simular(dados["dia"], dados["produtos"], dados["ofertas"], **parametros)
    return dict(**dados, **plano, perguntas=QUESTIONS,
                capital=sum(p["capital"] for p in dados["produtos"]),
                perda=sum(r["perda"] for r in dados["riscos"]),
                economia=sum(a["economia"] for a in plano["compras"]))


def snapshot():
    with connect() as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        return montar_painel(ler_cenario(db))


@app.get("/")
def home():
    return FileResponse(Path(__file__).with_name("index.html"))


@app.get("/assistente.js")
def script_assistente():
    return FileResponse(Path(__file__).with_name("assistente.js"), media_type="application/javascript")


@app.get("/painel")
def painel():
    return snapshot()


@app.get("/alertas")
def alertas():
    return snapshot()["alertas"]


class Pergunta(BaseModel):
    anterior: str = Field(default='',max_length=500)
    pergunta: str = Field(min_length=1,max_length=500)


def norm(s):
    return unicodedata.normalize("NFKD",s.lower()).encode("ascii","ignore").decode()


def route(q):
    for pergunta, intent in zip(QUESTIONS, INTENTS):
        if norm(q.strip()) == norm(pergunta):
            return intent, "Pergunta sugerida"
    for intent, pattern in [("fornecedores","fornecedor|compar"),("validade","venc|validade"),
        ("capital","capital|parado|imobilizado"),("alertas","estranh|alerta|temperatura|acesso"),
        ("compras","compr|reposi|repor")]:
        if re.search(pattern,norm(q)):
            return intent,"Regras locais"
    return "desconhecido","Regras locais"


def consulta_produto(query, painel, dados):
    """Consultas factuais simples. Questões compostas seguem para o modelo."""
    produtos = [p for p in painel["produtos"] if norm(p["nome"]) in query.replace(".", ",")]
    if len(produtos) != 1 or re.search(r"compar|por que|como|se eu|mais barato|vale comprar", query):
        return None
    p = produtos[0]
    nome = p["nome"]
    fornecedores = [f["nome"] for f in dados["fornecedores"] if norm(f["nome"]) in query]
    if len(fornecedores) == 1:
        oferta = next((o for o in dados["ofertas"] if o["produto"] == p["id"] and o["fornecedor"] == fornecedores[0]), None)
        if not oferta:
            return f"Não há oferta de {nome} para {fornecedores[0]}."
        if re.search(r"minimo|entrega|prazo|custa|preco|cotacao", query):
            return (f"{fornecedores[0]} oferece {nome} por {MONEY(oferta['preco'])} por unidade, "
                    f"com pedido mínimo de {oferta['minimo']} unidades e entrega em {oferta['prazo']} dias.")
        return None
    if "fornecedor" in query:
        return None
    if re.search(r"consumo medio|saem por dia|media diaria",query):
        media = f"{p['media']:.2f}".replace(".",",")
        return f"{nome}: média de {media} unidades por dia nos últimos 28 dias completos."
    if re.search(r"capital|valor em estoque",query):
        return f"O capital em estoque de {nome} é {MONEY(p['capital'])}, ao custo dos lotes válidos."
    if re.search(r"custo unitario|preco.*venda",query):
        cadastro = next(i for i in dados["produtos"] if i["id"] == p["id"])
        custo = "custo unitario" in query
        v = cadastro["custo" if custo else "preco_venda"]
        return f"{nome} • {'Custo unitário' if custo else 'Preço de venda cadastrado'}: {MONEY(v) if v is not None else 'não informado'}."
    if re.search(r"quais.*lotes|quando vence|validade do lote",query):
        lotes = [l for l in dados["lotes"] if l["produto"] == p["id"]]
        return nome + ": " + ("\n".join(f"Lote {l['id']} • validade {l['validade']:%d/%m/%Y} • saldo {l['unidades']}." for l in lotes) or "nenhum lote cadastrado.")
    if re.search(r"saldo|unidades.*disponiveis",query):
        return f"{nome} tem {p['estoque']} unidades disponíveis em lotes válidos."
    return None

def consultar(input: Pergunta, s):
    q = input.pergunta
    intent, origem = route(q)
    query = norm(q)
    # Normaliza perguntas factuais de um único assunto antes do roteamento.
    temas = {tema for tema, formas in FORMAS.items()
             if any(norm(frase) in query for frase in formas)}
    if len(temas) == 1 and not re.search(r"compar|por que|se eu", query):
        query += " " + CANONICA[temas.pop()]
    coverage = r"dura|cobertura|quantos dias|quanto tempo|acabar|aguenta|autonomia|sem repor|sem reposicao"
    follow = query.startswith(("e ", "e a", "e o")) and re.search(coverage,norm(input.anterior))
    if (re.search(coverage,query) and not re.search("entreg|validade|prazo",query)) or follow:
        produtos = s["produtos"]
        selected = [p for p in produtos if norm(p["nome"]) in query.replace(".",",")]
        if not selected:
            selected = [p for p in produtos if norm(p["nome"]).split()[0] in query] or produtos
        linhas = ["Estimativa sem reposição, pelo consumo dos últimos 28 dias completos:"]
        for p in sorted(selected,key=lambda p:cobertura(p) if p["media"] else math.inf):
            qtd,media=p["estoque"],p["media"]
            dias = f"{cobertura(p):.1f} dias".replace(".",",") if media else "sem estimativa (sem consumo registrado)"
            taxa = f"{media:.2f}".replace(".",",")
            linhas.append(f"{p['nome']} • {dias} ({qtd} unidades disponíveis, média diária de {taxa}).")
        linhas.append("A projeção considera o saldo válido hoje e pode cair se algum lote vencer antes do uso.")
        return dict(resposta="\n".join(linhas),origem="Saldo e retiradas",consulta="cobertura")
    guia = {"Produtos":"Em Produtos, use Cadastrar produto ou Editar produto para definir nome, custo e preço de venda.",
            "Entradas":"Em Produtos, use Registrar entrada para informar produto, fornecedor, cofre, quantidade, custo, data e validade.",
            "Vendas":"Em Produtos, use Registrar venda, escolha o lote e informe quantidade, data e preço unitário efetivo. Consulte o histórico em Movimentações.",
            "Fornecedores":"Em Fornecedores, salve o nome, CNPJ, endereço e contato. Em Registrar oferta, associe produto, preço, pedido mínimo e prazo de entrega.",
            "Visão estratégica":"A Visão estratégica mostra saldo por produto, capital ao custo, receita das vendas registradas, perdas projetadas e sugestões de compra."}
    if origem != "Pergunta sugerida":
        if re.search("^como (cadastr|registr|lancar|alterar|editar|utilizar)|^onde registro|em qual aba",query):
            topics=[key for pattern,key in [("fornecedor|oferta","Fornecedores"),("vend","Vendas"),("entrada|lote","Entradas"),("produto","Produtos"),("dashboard|painel|estrateg","Visão estratégica")] if re.search(pattern,query)]
            return dict(resposta="\n".join(guia[k] for k in topics or guia),origem="Guia do sistema",consulta="Orientação")
        dados=catalogo()
        factual = consulta_produto(query,s,dados)
        if factual:
            return dict(resposta=factual,origem="Consulta ao catálogo",consulta="produto")
        if intent == "desconhecido":
            intent, origem = interpretar(q, input.anterior)
    if intent=="compras":
        linhas=[f"{a['nome']} • {a['quantidade']} unidades de {a['fornecedor']} até {a['comprar_ate']:%d/%m}, por {MONEY(a['total'])}." for a in s["compras"]]
    elif intent=="validade":
        linhas=[f"Priorize o lote {r['lote']} de {r['nome']}. Há {r['unidades']:.0f} unidades em risco até {r['validade']:%d/%m}, equivalentes a {MONEY(r['perda'])}." for r in s["riscos"]]
    elif intent=="capital":
        linhas=[f"O estoque soma {MONEY(s['capital'])}."]+[f"{p['nome']} • {MONEY(p['capital'])}" for p in s["produtos"]]
    elif intent=="fornecedores":
        nome=next((n for n in s["comparacoes"] if norm(n) in norm(q)),None)
        if nome:
            ofertas=s["comparacoes"][nome]
            linhas=[f"{a['fornecedor']} • {a['quantidade']} unidades por {MONEY(a['total'])}, entrega em {a['prazo']} dias." for a in ofertas]
            viaveis=[a for a in ofertas if a["atende"]]
            if viaveis:
                melhor=min(viaveis,key=lambda a:a["total"])
                linhas.append(f"Para {nome}, prefira {melhor['fornecedor']} se for comprar agora.")
            else:
                linhas.append("Nenhuma oferta chega antes do fim do estoque.")
        else:
            linhas=["Informe o produto e a apresentação para comparar. Exemplo: semaglutida 1 mg."]
    elif intent=="alertas":
        linhas=[f"{a['cofre']} • {a['instante']:%d/%m %H:%M} • {a['motivo']}." for a in s["alertas"]]
    else:
        linhas=["Não tenho essa informação. Posso consultar compras, validade, capital, fornecedores e alertas."]
    return dict(resposta="\n".join(linhas) or "Nenhuma ocorrência no cenário.",origem=origem,consulta=intent)




def interpretar(q, anterior):
    if os.getenv("OLLAMA_ATIVO", "0" if os.getenv("VERCEL") else "1") == "0":
        return "desconhecido", "Regras locais"
    class Intencao(BaseModel):
        consulta: Literal["compras", "validade", "capital", "fornecedores", "alertas", "desconhecido"]
    prompt = ("Classifique a pergunta de gestão de estoque. Contexto anterior: " + anterior[-500:]
              + "\nPergunta: " + q)
    sistema = ("Identifique a intenção da pergunta atual. Use a anterior apenas para resolver referências. "
               "Exemplos: 'o que vai faltar?' = compras; 'o que vence primeiro?' = validade; "
               "'quanto dinheiro está parado?' = capital; 'quem entrega mais cedo?' = fornecedores; "
               "'houve algum incidente?' = alertas. Use desconhecido para assuntos fora dessas categorias.")
    body = dict(model=os.getenv("OLLAMA_MODEL","au-asist:7b"), system=sistema, prompt=prompt, stream=False,
                format=Intencao.model_json_schema(), options=dict(temperature=0))
    try:
        req = Request("http://host.docker.internal:11434/api/generate",json.dumps(body).encode(),
                      {"Content-Type":"application/json"})
        with urlopen(req,timeout=30) as r:
            return Intencao.model_validate_json(json.load(r)["response"]).consulta, "Ollama local"
    except (OSError, ValueError, KeyError, TypeError):
        return "desconhecido", "Regras locais"

from conversa import registrar_chat
registrar_chat(app, montar_painel, consultar, norm)
