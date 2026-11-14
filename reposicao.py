"""Regras compartilhadas pelo painel e pelo planejamento conversacional."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import ceil, floor

CENTAVO = Decimal("0.01")


def moeda(valor):
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def cobertura(p):
    return p["estoque"] / p["media"] if p["media"] else None


def simular(dia, produtos, ofertas, produtos_ids=(), horizonte=None, orcamento=None, fornecedor=None):
    compras, comparacoes, pendencias = [], {}, []
    restante = orcamento
    selecionados = [p for p in produtos if not produtos_ids or p["id"] in produtos_ids]
    def prioridade(p):
        dias = cobertura(p)
        return (dias if dias is not None else Decimal("Infinity"), p["id"])

    for p in sorted(selecionados, key=prioridade):
        nome, media, saldo = p["nome"], p["media"], p["estoque"]
        alternativas = comparacoes[nome] = []
        def pendencia(motivo):
            pendencias.append(dict(produto=p["id"], nome=nome, motivo=motivo))
        if not media:
            pendencia("Sem consumo nos últimos 28 dias. A necessidade não pode ser estimada.")
            continue
        for o in ofertas:
            if o["produto"] != p["id"]:
                continue
            prazo = o["prazo"]
            dias = horizonte or (14 if o["anterior"] and o["preco"] < o["anterior"] * Decimal(".95") else 30)
            seguranca = Decimal("1.65") * p["desvio"] * Decimal(prazo).sqrt()
            alvo = ceil(media * (prazo + dias) + seguranca - saldo)
            quantidade = max(o["minimo"], alvo)
            preco = moeda(o["preco"])
            if preco <= 0:
                continue
            alternativas.append(dict(produto=p["id"], nome=nome, fornecedor=o["fornecedor"],
                quantidade=quantidade, minimo=o["minimo"], unitario=preco, total=quantidade*preco,
                prazo=prazo, atende=prazo*media<=saldo, horizonte=dias,
                repor=alvo>0 if horizonte else saldo<media*prazo+seguranca,
                comprar_ate=dia+timedelta(days=max(0, floor(saldo/media)-prazo))))
        if not alternativas:
            pendencia("Sem oferta válida na data do cenário.")
            continue
        candidatas = [a for a in alternativas if not fornecedor or a["fornecedor"] == fornecedor]
        if not candidatas:
            pendencia(f"{fornecedor} não tem oferta para este produto.")
            continue
        if restante is not None:
            if not any(a["repor"] for a in candidatas):
                continue
            candidatas = [a for a in candidatas if a["repor"] and a["minimo"]*a["unitario"] <= restante]
            if not candidatas:
                pendencia("O pedido mínimo excede o orçamento disponível.")
                continue
        escolha = min(candidatas, key=lambda a:(not a["atende"], 0 if a["atende"] else a["prazo"], a["total"], a["fornecedor"]))
        if not escolha["repor"]:
            continue
        item = escolha.copy()
        if fornecedor and any(a["atende"] and a["total"] < item["total"] for a in alternativas):
            pendencia("Há uma oferta com menor total que chega antes da ruptura.")
        if not item["atende"]:
            pendencia("A entrega prevista ocorre depois da ruptura.")
        if restante is not None:
            qtd = min(item["quantidade"], floor(restante/item["unitario"]))
            if qtd < item["quantidade"]:
                pendencia(f"Faltam {item['quantidade']-qtd} unidades para atender a sugestão.")
            item.update(quantidade=qtd, total=qtd*item["unitario"])
            restante -= item["total"]
        item["economia"] = max(0,(alternativas[0]["unitario"]-item["unitario"])*item["quantidade"])
        compras.append(item)
    return dict(compras=compras, comparacoes=comparacoes, pendencias=pendencias)
