"""Regressão de consultas sobre o cenário de demonstração. Execute com a API local ativa."""
import concurrent.futures
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

def api(path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = Request(os.getenv("AU_TEST_URL","http://localhost:8000")+"/"+path, body, {"Content-Type":"application/json"})
    with urlopen(req, timeout=65) as response:
        return json.load(response)

def br(v):
    return f"{float(v):,.2f}".replace(",","_").replace(".",",").replace("_",".")

def cases():
    painel, catalogo = api("painel"), api("catalogo")
    for p in painel["produtos"]:
        nome=p["nome"]
        record=next(x for x in catalogo["produtos"] if x["id"]==p["id"])
        lotes=[l for l in catalogo["lotes"] if l["produto"]==p["id"]]
        queries=[
            ("Quantas unidades de {} estão disponíveis?",f"{int(p['estoque'])} unidades"),
            ("Qual o saldo de {}?",f"{int(p['estoque'])} unidades"),
            ("Quanto tempo dura o estoque de {}?",f"{float(p['estoque'])/float(p['media']):.1f}".replace(".",",")+" dias"),
            ("Qual é a autonomia de {} sem reposição?",f"{float(p['estoque'])/float(p['media']):.1f}".replace(".",",")+" dias"),
            ("Qual o consumo médio diário de {}?",br(p["media"])+" unidades"),
            ("Quantas unidades de {} saem por dia?",br(p["media"])+" unidades"),
            ("Quanto capital está imobilizado em {}?","R$ "+br(p["capital"])),
            ("Qual o valor em estoque de {}?","R$ "+br(p["capital"])),
            ("Qual o custo unitário de {}?","R$ "+br(record["custo"])),
            ("Qual o preço cadastrado de venda de {}?", "não informado" if record["preco_venda"] is None else "R$ "+br(record["preco_venda"])),
            ("Quais são os lotes de {}?",f"Lote {lotes[0]['id']}"),
            ("Quando vence o lote de {}?", "/".join(lotes[0]["validade"].split("-")[::-1])),
        ]
        for o in catalogo["ofertas"]:
            if o["produto"]!=p["id"]: continue
            f=o["fornecedor"]
            queries.extend([(f"Quanto custa {{}} no {f}?","R$ "+br(o["preco"])),
                (f"Qual o prazo de entrega de {{}} no {f}?",f"{o['prazo']} dias"),
                (f"Qual o pedido mínimo de {{}} do {f}?",f"{o['minimo']} unidades"),
                (f"Em quantos dias o {f} entrega {{}}?",f"{o['prazo']} dias")])
        for template,expected in queries:
            yield template.format(nome),[nome,expected]

    extras=[
        ("Meu estoque atual dura quanto tempo sem reposição?",["28 dias","Liraglutida"]),
        ("Como cadastrar um fornecedor?",["Fornecedores","CNPJ"]),
        ("Onde registro uma venda?",["Produtos","Registrar venda"]),
        ("Como lançar uma entrada?",["Produtos","Registrar entrada"]),
        ("Em qual aba consulto o dashboard?",["Visão estratégica","receita"]),
        ("Como alterar o custo de um produto?",["Produtos","Editar produto"]),
        ("O que preciso comprar hoje?",[painel["compras"][0]["nome"]] if painel["compras"] else ["Nenhuma"]),
        ("Quais lotes estão em risco de vencer?",[painel["riscos"][0]["nome"]] if painel["riscos"] else ["Nenhuma"]),
        ("Quanto dinheiro está parado no estoque?",["R$ "+br(painel["capital"])]),
        ("Aconteceu algo estranho nos cofres esta semana?",[painel["alertas"][0]["cofre"]] if painel["alertas"] else ["Nenhuma"]),
    ]
    yield from extras

def check(case):
    q,expected=case
    try:
        response=api("chat",{"pergunta":q})
        answer=response["resposta"]
        ok=all(v.casefold() in answer.casefold() for v in expected)
        return dict(pergunta=q,esperado=expected,resposta=answer,origem=response["origem"],passou=ok)
    except Exception as e:
        return dict(pergunta=q,erro=str(e),passou=False)

if __name__=="__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        rows=list(pool.map(check,cases()))
    Path(sys.argv[1]).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf8")
    print(sum(r["passou"] for r in rows),"/",len(rows))
