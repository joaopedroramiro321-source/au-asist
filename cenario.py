def ler_cenario(db):
    dia = db.execute("SELECT dia FROM cenario").fetchone()["dia"]
    produtos = db.execute("""SELECT p.id,nome,media,desvio,coalesce(sum(unidades),0) AS estoque,
      coalesce(sum(unidades*s.custo),0) AS capital FROM produtos p JOIN consumo c USING(id)
      LEFT JOIN saldo s ON s.produto=p.id AND validade>=%s GROUP BY p.id,media,desvio ORDER BY p.id""",(dia,)).fetchall()
    riscos = db.execute("""SELECT r.produto,nome,r.id AS lote,validade,em_risco AS unidades, em_risco*r.custo AS perda
      FROM risco r JOIN produtos p ON p.id=r.produto WHERE em_risco>0 ORDER BY validade""").fetchall()
    ofertas = db.execute("""SELECT DISTINCT ON(produto,fornecedor) a.*,
      (SELECT preco FROM precos b WHERE b.produto=a.produto AND b.fornecedor=a.fornecedor
      AND b.data<=a.data-30 ORDER BY data DESC LIMIT 1) AS anterior
      FROM precos a WHERE data<=%s ORDER BY produto,fornecedor,data DESC""",(dia,)).fetchall()
    alertas = db.execute("""SELECT c.nome AS cofre,instante,'Retirada fora do expediente' AS motivo
     FROM retiradas r JOIN lotes l ON l.id=r.lote JOIN cofres c ON c.id=l.cofre
     WHERE instante::date BETWEEN %s-6 AND %s AND instante::time NOT BETWEEN '09:00' AND '18:00'
     UNION ALL SELECT c.nome,min(instante),'Temperatura acima de 8 °C ou abaixo de 2 °C'
     FROM temperatura t JOIN cofres c ON c.id=t.cofre WHERE graus NOT BETWEEN 2 AND 8
     AND instante::date BETWEEN %s-6 AND %s GROUP BY c.nome,instante::date ORDER BY 2""",(dia,)*4).fetchall()
    return dict(dia=dia, produtos=produtos, riscos=riscos, ofertas=ofertas, alertas=alertas)
