CREATE TABLE IF NOT EXISTS fornecedores(nome text PRIMARY KEY, cnpj text UNIQUE, endereco text, contato text);
INSERT INTO fornecedores(nome) SELECT DISTINCT fornecedor FROM precos ON CONFLICT DO NOTHING;
ALTER TABLE lotes ADD COLUMN IF NOT EXISTS entrada date, ADD COLUMN IF NOT EXISTS fornecedor text REFERENCES fornecedores;
ALTER TABLE retiradas ADD COLUMN IF NOT EXISTS preco_venda numeric;
ALTER TABLE produtos ADD COLUMN IF NOT EXISTS preco_venda numeric;
CREATE SEQUENCE IF NOT EXISTS produto_seq;
CREATE SEQUENCE IF NOT EXISTS lote_seq;
SELECT setval('produto_seq',greatest((SELECT max(id) FROM produtos),(SELECT last_value FROM produto_seq)));
SELECT setval('lote_seq',greatest((SELECT max(id) FROM lotes),(SELECT last_value FROM lote_seq)));
ALTER TABLE produtos ALTER COLUMN id SET DEFAULT nextval('produto_seq');
ALTER TABLE lotes ALTER COLUMN id SET DEFAULT nextval('lote_seq');

CREATE TABLE IF NOT EXISTS conversas (
    id uuid PRIMARY KEY, estado jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS mensagens (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversa uuid NOT NULL REFERENCES conversas, pergunta text NOT NULL, resposta jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS mensagens_conversa ON mensagens(conversa,id);
CREATE TABLE IF NOT EXISTS propostas (
    id uuid PRIMARY KEY, conversa uuid NOT NULL REFERENCES conversas,
    status text NOT NULL CHECK(status IN ('rascunho','confirmada','cancelada')),
    versao integer NOT NULL, dia date NOT NULL, parametros jsonb NOT NULL,
    revisao jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS proposta_itens (
    proposta uuid REFERENCES propostas, produto integer NOT NULL REFERENCES produtos,
    fornecedor text NOT NULL, quantidade integer NOT NULL CHECK(quantidade>0),
    unitario numeric NOT NULL, total numeric NOT NULL, PRIMARY KEY(proposta,produto)
);

ALTER TABLE conversas ADD COLUMN IF NOT EXISTS dono text;
