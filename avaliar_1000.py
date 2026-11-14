"""Executar no contêiner app. Avalia o endpoint em transação descartada."""
import json
from contextlib import nullcontext
from unittest.mock import patch
import conversa, gestao, main
from formas_assistente import FORMAS

chat = next(r.endpoint for r in main.app.routes if r.path == "/chat")
with gestao.connect() as db:
    with patch.object(conversa, "connect", return_value=nullcontext(db)), patch.object(gestao, "connect", return_value=nullcontext(db)):
        painel, cat = main.snapshot(), gestao.catalogo()
        # Todas as perguntas usam o mesmo cenário imutável nesta transação.
        from contextlib import ExitStack
        snapshot = main.ler_cenario(db)
        with ExitStack() as cache:
            cache.enter_context(patch.object(conversa, "ler_cenario", side_effect=lambda _: dict(snapshot)))
            cache.enter_context(patch.object(main, "catalogo", return_value=cat))
            # Mantém os patches durante toda a rodada.
            patches = cache.pop_all()
        resultados = []
        for p in painel["produtos"][:5]:
            nome = p["nome"]
            cadastro = next(i for i in cat["produtos"] if i["id"] == p["id"])
            lotes = [i for i in cat["lotes"] if i["produto"] == p["id"]]
            ofertas = [i for i in cat["ofertas"] if i["produto"] == p["id"]]
            for tipo, formas in FORMAS.items():
                for frase in formas:
                    for indice in range(2):
                        oferta = ofertas[indice]
                        alvo = nome
                        if tipo in ("oferta","prazo","minimo"):
                            alvo += " no " + oferta["fornecedor"]
                        q = (f"Sobre {alvo}, {frase}?" if indice == 0 else f"{frase.capitalize()} para {alvo}?")
                        venda = cadastro["preco_venda"]
                        esperado = dict(saldo=f"{p['estoque']} unidades", cobertura=f"{main.cobertura(p):.1f} dias".replace(".",","),consumo=f"{p['media']:.2f} unidades".replace(".",","), capital=main.MONEY(p["capital"]), custo=main.MONEY(cadastro["custo"]), venda=main.MONEY(venda) if venda is not None else "não informado", lotes=f"Lote {lotes[0]['id']}",oferta=main.MONEY(oferta["preco"]),prazo=f"{oferta['prazo']} dias",minimo=f"{oferta['minimo']} unidades")[tipo]
                        r = chat(conversa.Entrada(pergunta=q))
                        ok = esperado in r.resposta and nome in r.resposta and (alvo == nome or oferta["fornecedor"] in r.resposta)
                        resultados.append(dict(pergunta=q,tema=tipo,esperado=esperado,resposta=r.resposta,passou=ok))
        db.rollback()
        patches.close()
assert len({r["pergunta"] for r in resultados}) == 1000
print(json.dumps(resultados, ensure_ascii=True))
