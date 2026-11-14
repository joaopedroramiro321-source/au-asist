BEGIN;
CREATE TABLE cenario AS SELECT current_date AS dia;
CREATE TABLE produtos(id int PRIMARY KEY, nome text NOT NULL, custo numeric NOT NULL);
INSERT INTO produtos VALUES (1,'Tirzepatida',1050),(2,'Semaglutida 0,5 mg',680),(3,'Semaglutida 1 mg',820),(4,'Liraglutida',390),(5,'Adalimumabe',1750);
CREATE TABLE cofres(id int PRIMARY KEY, nome text NOT NULL);
INSERT INTO cofres VALUES (1,'Cofre 1'),(2,'Cofre 2');
CREATE TABLE lotes(id int PRIMARY KEY, produto int REFERENCES produtos, cofre int REFERENCES cofres, validade date, inicial int, custo numeric);
CREATE TABLE retiradas(lote int REFERENCES lotes, instante timestamp, usuario text, qtd int CHECK(qtd>0));
CREATE TABLE precos(produto int REFERENCES produtos, fornecedor text, data date, preco numeric, minimo int, prazo int, PRIMARY KEY(produto,fornecedor,data));
CREATE TABLE temperatura(cofre int REFERENCES cofres, instante timestamp, graus numeric);
INSERT INTO lotes SELECT id,id,1+id%2,current_date+CASE WHEN id=2 THEN 21 ELSE 180 END,0,custo FROM produtos;
INSERT INTO lotes VALUES(6,2,1,current_date+150,20,680);
INSERT INTO retiradas SELECT id,current_date-d+time '11:00','Ana',
 CASE WHEN id=2 THEN 1+(d%5=0)::int ELSE 1+(d%2) END
 FROM produtos CROSS JOIN generate_series(1,90) d;
INSERT INTO retiradas VALUES (1,current_date-1+time '03:12','Ana',1);
UPDATE lotes l SET inicial=(SELECT sum(qtd) FROM retiradas WHERE lote=l.id)
 + (ARRAY[6,40,12,4,60])[l.id] WHERE id<=5;
INSERT INTO precos SELECT id,f,current_date-d,
 round(custo*CASE WHEN id=4 AND d=0 THEN 0.90 ELSE 1 END*
 CASE WHEN f='Fornecedor B' THEN 0.90 ELSE 1 END,2),
 CASE WHEN f='Fornecedor B' THEN 12 ELSE 1 END,
 CASE WHEN f='Fornecedor B' THEN 3 ELSE 2 END
 FROM produtos CROSS JOIN (VALUES('Fornecedor A'),('Fornecedor B')) a(f) CROSS JOIN (VALUES(30),(0)) b(d);
INSERT INTO temperatura SELECT 2,current_date-1+time '14:00'+n*interval '5 min',
 CASE WHEN n BETWEEN 1 AND 7 THEN 9.4 ELSE 5.0 END FROM generate_series(0,8)n;
CREATE VIEW saldo AS SELECT l.*, inicial-coalesce((SELECT sum(qtd) FROM retiradas WHERE lote=l.id),0) AS unidades FROM lotes l;
CREATE VIEW consumo AS
 WITH diario AS (SELECT p.id,d,coalesce(sum(r.qtd),0) AS qtd FROM produtos p
 CROSS JOIN cenario CROSS JOIN generate_series(1,28)d
 LEFT JOIN lotes l ON l.produto=p.id LEFT JOIN retiradas r ON r.lote=l.id AND r.instante::date=dia-d
 GROUP BY p.id,d)
 SELECT id,avg(qtd) AS media,stddev_samp(qtd) AS desvio FROM diario GROUP BY id;
CREATE VIEW risco AS
 WITH fila AS (SELECT s.*,dia,media,coalesce(sum(unidades) OVER(PARTITION BY produto ORDER BY validade,s.id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) AS anteriores
 FROM saldo s JOIN consumo c ON c.id=s.produto CROSS JOIN cenario WHERE validade>=dia)
 SELECT *,greatest(0,unidades-greatest(0,floor(media*(validade-dia))-anteriores)) AS em_risco FROM fila;
COMMENT ON VIEW risco IS 'Unidades em risco após alocar o consumo aos lotes por ordem de validade.';
COMMENT ON VIEW consumo IS 'Consumo por dia nos 28 dias completos anteriores à data da simulação.';
COMMIT;
