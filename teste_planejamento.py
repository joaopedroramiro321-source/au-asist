"""Teste integrado em esquema temporário com rollback. Execute dentro do serviço app."""
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
from uuid import uuid4

import conversa
import gestao
import main as sistema
from conversa import Entrada
from reposicao import simular


class Planejamento(TestCase):
    def setUp(self):
        self.db = gestao.connect()
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)
        schema = "qa_" + uuid4().hex
        self.db.execute("CREATE SCHEMA " + schema)
        self.db.execute("SET LOCAL search_path TO " + schema)
        self.db.execute("CREATE TABLE cenario AS SELECT current_date AS dia")
        self.db.execute("CREATE TABLE produtos(id int PRIMARY KEY,nome text,custo numeric)")
        self.db.execute("INSERT INTO produtos VALUES (1,'Teste 1 mg',4),(2,'Teste 2 mg',9)")
        self.db.execute("CREATE TABLE precos(produto int, fornecedor text,data date,preco numeric,minimo int,prazo int)")
        self.db.execute("INSERT INTO precos VALUES (1,'Fornecedor B',current_date,4,2,1)")
        self.db.execute("CREATE TABLE lotes(id int PRIMARY KEY, produto int,cofre int,validade date,inicial int,custo numeric)")
        self.db.execute("INSERT INTO lotes VALUES (1,1,1,current_date+30,1,4)")
        self.db.execute("CREATE TABLE retiradas(lote int,instante timestamp,usuario text,qtd int)")
        self.db.execute("CREATE TABLE cofres(id int,nome text)")
        self.db.execute("CREATE TABLE temperatura(cofre int,instante timestamp,graus numeric)")
        self.db.execute("CREATE VIEW saldo AS SELECT *, inicial AS unidades FROM lotes")
        self.db.execute("CREATE VIEW consumo AS SELECT id,1::numeric media,0::numeric desvio FROM produtos")
        self.db.execute("CREATE VIEW risco AS SELECT *,0 AS em_risco FROM saldo")
        self.db.execute(Path("migration.sql").read_text(encoding="utf-8-sig"))
        for module in (conversa,gestao):
            self.enterContext(patch.object(module,"connect",return_value=nullcontext(self.db)))
        self.chat = next(r.endpoint for r in sistema.app.routes if r.path=="/chat")

    def enviar(self,q,cid=None,acao=None):
        return self.chat(Entrada(pergunta=q,conversa_id=cid,acao=acao))

    def test_revisao_isolamento_e_confirmacao(self):
        with patch.object(sistema,"urlopen",side_effect=AssertionError("consulta chamou o modelo")):
            inicial = self.enviar("O que comprar?")
            cid = inicial.conversa_id
            self.enviar("Até R$ 50",cid)
            self.enviar("Para 15 dias",cid)
            preferencia = self.enviar("Fornecedor B",cid)
            self.assertEqual(preferencia.contexto["orcamento"],"50.00")
            self.assertEqual(preferencia.contexto["horizonte_dias"],15)
            self.assertEqual(preferencia.contexto["fornecedor"],"Fornecedor B")
            outro = self.enviar("Para 7 dias")
            self.assertNotEqual(cid,outro.conversa_id)
            self.assertIsNone(outro.contexto["orcamento"])
            self.assertEqual(self.enviar("Comparar Teste",cid).consulta,"esclarecimento")
            draft = self.enviar("Preparar proposta",cid)
            self.assertLessEqual(draft.cartoes[0].total,Decimal(50))
            confirmar = next(a for a in draft.acoes if a.tipo=="confirmar")
            self.db.execute("UPDATE precos SET preco=5")
            revisado = self.enviar("Confirmar",cid,confirmar)
            self.assertEqual(revisado.cartoes[0].status,"rascunho")
            self.assertEqual(revisado.cartoes[0].versao,2)
            self.assertEqual(self.enviar("Confirmar",cid,confirmar).cartoes[0].status,"rascunho")
            confirmar = next(a for a in revisado.acoes if a.tipo=="confirmar")
            antes = self.db.execute("SELECT * FROM saldo ORDER BY id").fetchall()
            for _ in range(2):
                self.assertEqual(self.enviar("Confirmar",cid,confirmar).cartoes[0].status,"confirmada")
            self.assertEqual(antes,self.db.execute("SELECT * FROM saldo ORDER BY id").fetchall())
            self.assertEqual(self.db.execute("SELECT count(*) AS n FROM propostas").fetchone()["n"],1)
            self.assertEqual(self.db.execute("SELECT count(*) AS n FROM retiradas").fetchone()["n"],0)

    def test_pendencias_e_limites(self):
        r = self.enviar("Para 0 dias")
        self.assertEqual(r.consulta,"esclarecimento")
        self.assertEqual(self.enviar("Até R$ -1").consulta,"esclarecimento")
        r = self.enviar("Para 2 dias")
        self.assertTrue(any("Sem oferta" in getattr(c,"motivo","") for c in r.cartoes))
        limite = self.enviar("Até R$ 7",r.conversa_id)
        self.assertEqual(limite.cartoes[0].total,0)
        self.assertTrue(any("mínimo" in getattr(c,"motivo","") for c in limite.cartoes))
        p = dict(id=1,nome="Teste",estoque=0,media=Decimal(0),desvio=Decimal(0))
        resultado = simular(None,[p],[])
        self.assertIn("Sem consumo",resultado["pendencias"][0]["motivo"])

    def test_modelo_indisponivel_ou_invalido(self):
        with patch.dict("os.environ",OLLAMA_ATIVO="1"):
            from io import BytesIO
            for falha in (OSError(),TimeoutError(),None):
                with patch.object(sistema,"urlopen",side_effect=falha,return_value=BytesIO(b'{"response":"{}"}')):
                    self.assertEqual(sistema.interpretar("pergunta aberta",""),("desconhecido","Regras locais"))


if __name__=="__main__":
    main()
