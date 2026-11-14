# AU Asist — Assistente de compras
O AU Asist ajuda equipes de compras e estoque de farmácias a decidir quais produtos repor, em que quantidade e com qual fornecedor. O painel reúne consumo, saldos por lote, validade e preços. O assistente usa esses mesmos dados para explicar as recomendações e registrar propostas internas.

## Arquitetura

O navegador carrega HTML, CSS e JavaScript e consulta a API FastAPI na mesma origem. O backend Python calcula a reposição e acessa o PostgreSQL via psycopg. O classificador Ollama é opcional na instalação local. As propostas confirmadas ficam no banco e não geram pedidos externos ou pagamentos.

## Executar localmente

Copie `.env.example` para `.env` e defina `POSTGRES_PASSWORD` com uma senha alfanumérica própria antes de criar o volume. Em instalações existentes, mantenha a senha do volume atual. No Windows, inicie o Docker Desktop e execute nesta pasta:

```powershell
docker compose up --build -d
```

Abra http://localhost:8000. A primeira preparação baixa as imagens e dependências. Após a preparação, use `docker compose up -d` para operar offline. Para parar, use `docker compose down`. O volume mantém o cenário e sua data de referência.

O painel lateral do assistente consulta o estoque, compara ofertas e prepara propostas internas. A conversa e suas mensagens ficam no PostgreSQL. A aba do navegador mantém o identificador da conversa para recuperar o histórico após recarregar. “Nova conversa” inicia um contexto independente.

As consultas operacionais e o planejamento funcionam localmente. Para classificar perguntas abertas, inicie o Ollama no computador e baixe o modelo com `ollama pull qwen2.5:7b` e crie a versão especializada com `ollama create au-asist:7b -f Modelfile`. Ele recebe exemplos de intenções, a pergunta atual e até 500 caracteres da anterior. O backend valida a classificação e calcula os números. Em máquinas com pouca memória, defina `OLLAMA_MODEL=qwen2.5:3b` no arquivo `.env` do Compose e baixe essa versão. Para desativar a integração, use `OLLAMA_ATIVO=0`. Aplique mudanças com `docker compose up -d --build app`.

Cadastre produtos, registre entradas e vendas por lote na aba Produtos. A aba Fornecedores reúne contatos e ofertas por produto. As datas são interpretadas em relação ao dia do cenário exibido no cabeçalho. A migração preserva os dados existentes. Retiradas históricas sem preço não entram na receita.

Uma farmácia simulada tem dois cofres, cinco produtos e 90 dias de retiradas. O painel calcula cobertura e reposição com consumo dos últimos 28 dias. O pedido mínimo é um piso de quantidade. Para comparação, simula-se uma compra agora, mesmo quando a reposição ainda pode esperar. A economia exibida é a diferença de preço para a mesma quantidade no fornecedor A. A perda potencial por validade é apresentada separadamente.

### Roteiro de demonstração

1. Em Visão estratégica, abra o assistente e selecione “O que preciso comprar?”. Confira os itens com a tabela de ações de compra.
2. Digite “Até R$ 500” e depois “Para 15 dias”. Observe o total e os produtos que o orçamento deixou descobertos.
3. Selecione “Comparar fornecedores”. Digite “Prefiro o Fornecedor B” para ver o efeito do mínimo. Digite “Sem preferência” para voltar a considerar todas as ofertas.
4. Selecione “Preparar proposta”. Digite “Até R$ 800” e revise a nova versão. Use “Confirmar planejamento” e confira o identificador. Feche e reabra o painel para recuperar a conversa.
5. Em Produtos, selecione “Analisar este produto” para consultar sua cobertura.

### Regras e limites do MVP

- A cobertura atual é o saldo válido dividido pela média diária. Um horizonte explícito de 1 a 365 dias define o saldo-alvo **após a entrega**. A necessidade é a média diária multiplicada por prazo mais horizonte, acrescida de 1,65 desvios diários vezes a raiz do prazo, descontando o saldo atual. Arredondamos a quantidade para cima e aplicamos o mínimo. No painel, a regra original dispara no ponto de reposição e usa 30 dias, ou 14 se o preço caiu mais de 5% frente à referência de 30 dias.
- Com orçamento, a heurística começa pela menor cobertura e considera o prazo, o mínimo e o total das ofertas. Prioriza entregas a tempo e limita a quantidade pelo dinheiro restante. Preços são arredondados ao centavo com Decimal e ROUND_HALF_UP. A preferência restringe as ofertas elegíveis. O resultado é uma heurística determinística.
- O saldo inclui os lotes válidos no dia do cenário. Vencimentos futuros podem reduzir a cobertura efetiva. Os cartões de validade apresentam a projeção de perdas separadamente.
- A edição do rascunho ocorre por parâmetros de simulação. A confirmação grava um planejamento interno e preserva os lotes e as movimentações. Mudanças nos dados relevantes exigem revisar a versão recalculada. Na instalação local, quem possui o identificador pode acessar a conversa. Na Vercel, o histórico e a continuidade também exigem o cookie de sessão do navegador. Não há contas de usuários ou controle de acesso para uso clínico. Utilize apenas dados de demonstração.

### Validação

Com o Compose ativo, execute no PowerShell:

```powershell
python teste_assistente.py "$env:TEMP\einsan-regressao.json"
$OutputEncoding = [Text.UTF8Encoding]::new()
Get-Content -Raw -Encoding utf8 teste_planejamento.py | docker compose exec -T app python -
```

A regressão usa o cenário de demonstração. A suíte de planejamento cria um esquema isolado e desfaz sua transação, inclusive os dados de teste, ao terminar.


## Publicação na Vercel e PostgreSQL Neon

A raiz do repositório contém a aplicação FastAPI (`main:app`), com Python 3.12 e dependências em `requirements.txt`. Na primeira instalação, execute `db/01_demo.sql` uma vez em um banco dedicado vazio, seguido de `migration.sql`, usando uma conexão direta. Na Vercel as funções usam a conexão agrupada do Neon com TLS. O servidor não executa migrações durante o atendimento.

| Variável | Uso |
| --- | --- |
| `DATABASE_URL` | Segredo de produção da Vercel para conectar a API ao PostgreSQL. O Compose monta a URL local. |
| `POSTGRES_PASSWORD` | Senha do PostgreSQL local usada pelo Compose. |
| `OLLAMA_ATIVO` | Use `0` para operar com as regras do assistente. O classificador local pode ser habilitado com `1`. |
| `OLLAMA_MODEL` | Nome do modelo instalado no Ollama local. |

O cliente não precisa de variáveis de ambiente. Na Vercel, as rotas de alteração de catálogo, ofertas, entradas e vendas ficam bloqueadas, e a consulta de fornecedores omite documentos e contatos. O planejamento continua disponível para o visitante. Não há integração de pagamentos, split ou webhooks.

Demonstração pública para os avaliadores: [abrir AU Asist](https://au-asist.vercel.app).
