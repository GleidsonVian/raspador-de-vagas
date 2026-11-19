# 🤖 Raspador de Vagas RPA & Tech

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Playwright](https://img.shields.io/badge/Playwright-Automation-2EAD33.svg?logo=playwright&logoColor=white)](https://playwright.dev/)
[![SQLite](https://img.shields.io/badge/SQLite-Local_Storage-003B57.svg?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Status](https://img.shields.io/badge/Status-Ativo-success.svg)](#)

Sistema autônomo de busca, filtragem e curadoria de vagas **100% remotas** em múltiplas plataformas de emprego (**Glassdoor, LinkedIn, Gupy e Programathor**). 

O raspador calcula a **aderência exata (% de Match)** de cada oportunidade ao seu perfil técnico (pesos por tecnologias, penalidades por lacunas e leitura contextual por seções), identifica salários e tipo de contratação (PJ/CLT), detecta vagas duplicadas e expiradas, e centraliza tudo em um **painel web local interativo e em tempo real**.

---

## 📸 Demonstração Visual das Telas

### 1. Painel de Vagas & Abas de Candidatura
Centralize o funil das suas candidaturas. Mude o status da vaga (*Nova*, *Interessado*, *Candidatado*, *Descartado*) e ela se move suavemente para a respectiva aba, sumindo da sua visão imediata para manter o foco no que importa.
![Painel de Vagas](docs/painel_vagas.png)

### 2. Leitura Inteligente com Destaque Colorido na Descrição
Abra a descrição completa com 1 clique e veja de imediato:
- 🟩 **Suas habilidades** encontradas no texto (marcadas em verde sutil).
- 🟥 **Lacunas e requisitos faltantes** que você não tem (fundo vermelho com sublinhado ondulado).
- 🟨 **Seções-chave** (*Requisitos, Qualificações, Diferenciais, Atividades*) destacadas em amarelo.
- 🎯 **Card explicativo:** detalha exatamente o porquê da pontuação final da vaga.
![Destaque na Descrição](docs/painel_descricao.png)

### 3. Analytics & Tendências de Mercado
Tenha uma visão estratégica do mercado de RPA e tecnologia a partir das centenas de vagas mineradas no seu banco de dados:
- **Top 12 Tecnologias Mais Demandadas**: frequência de menção (Python, SQL, UiPath, APIs, IA/GenAI, etc.).
- **Médias Salariais Estimadas**: comparativo real de valores médios para CLT e PJ.
- **Distribuição de Contratos e Senioridades**: proporção de vagas para Júnior, Pleno e Sênior.
- **Top Empresas Contratantes**: ranking com atalho clicável para filtrar as vagas da empresa.
- **Desempenho por Plataforma**: volume total, taxa de match médio e vagas relevantes por fonte.
![Painel de Estatísticas](docs/painel_estatisticas.png)

### 4. Configurações & Gestão Multi-Fonte
Ajuste facilmente termos de busca específicos para cada plataforma, idade máxima das vagas, pontuação mínima e ative/desative fontes sem precisar editar arquivos de código.
![Configurações](docs/painel_config.png)

---

## ⚡ Principais Recursos

- 🌐 **Múltiplas Fontes Integradas**:
  - **Glassdoor**: Coleta via CDP conectando-se ao Microsoft Edge já instalado.
  - **LinkedIn**: Coleta pública sem necessidade de login ou credenciais.
  - **Gupy Portal**: Integração direta com a API REST pública (`portal.gupy.io`), ultra veloz.
  - **Programathor**: Extração de oportunidades tech/remotas com headers atualizados.
- 🛡️ **Resiliência Anti-Bloqueio (HTTP 429)**:
  - Delays humanizados com jitter aleatório e emulação de navegadores modernos (Chrome 131 Client Hints).
  - Pausa inteligente com **backoff exponencial** (3h, 6h, 12h, 24h) e botão **"Retomar agora"** no painel quando a restrição expirar.
- 💰 **Extração Automática de Metadados**:
  - Identificação precisa de modelo de contratação: **PJ**, **CLT**, **CLT / PJ** ou **Cooperado**.
  - Detecção e extração de faixas salariais em reais (`💰 R$ 5.000 a R$ 8.000`).
- 🔍 **Deduplicação Cross-Site**:
  - Detecta quando a mesma empresa publica a mesma vaga em portais diferentes (ex: Gupy vs LinkedIn vs Glassdoor).
  - Oculta duplicatas por padrão na interface, com opção de habilitar o checkbox **"ver duplicadas"**.
- 👻 **Detector de Vagas Expiradas (Ghost Job Detector)**:
  - Botão **"🔍 Checar vagas ativas"** que testa os links das vagas em background e marca automaticamente como `Encerrada` aquelas que retornarem HTTP 404 ou avisos de processo finalizado.
- 🎯 **Algoritmo de Match Contextual (NLP Leve)**:
  - Divide a descrição em seções: *Requisitos* pesam 100%, *Diferenciais* pesam 30%, e *Benefícios/Sobre a Empresa* não contam.
  - Calcula cobertura de habilidades, penaliza lacunas e ajusta pontuação conforme a senioridade no título.
- 🔒 **Segurança Local & Trava Entre Processos**:
  - Painel isolado estritamente em `127.0.0.1` com proteção contra DNS Rebinding e CSRF.
  - Trava nativa do Windows (Named Mutex) impedindo que o painel, terminal ou agendador colidam entre si.

---

## 🛠️ Instalação

### Pré-requisitos
- **Windows** (utiliza o Microsoft Edge nativo e APIs de sincronização do Windows).
- **Python 3.10+**.

```bash
# Clone o repositório
git clone https://github.com/GleidsonVian/raspador-de-vagas.git
cd raspador-de-vagas

# Crie e ative um ambiente virtual (opcional, mas recomendado)
python -m venv .venv
.venv\Scripts\activate

# Instale as dependências
pip install -r requirements.txt
```

---

## 🚀 Como Usar

### 1. Painel Web (Recomendado)
Para iniciar a interface web:
```bash
python -m raspador
```
ou simplesmente dê um duplo clique no executável `iniciar_painel.bat`.

O painel será aberto automaticamente no seu navegador em:
👉 **`http://127.0.0.1:8765`**

No painel, clique em **"Rodar agora"** para iniciar a captura de todas as fontes ou selecione fontes específicas no menu dropdown.

### 2. Linha de Comando (CLI)
Você também pode rodar buscas pontuais via terminal:
```bash
python cacador.py                          # Todas as fontes ativas e termos
python cacador.py --fonte gupy             # Apenas uma fonte específica
python cacador.py --termo rpa --termo n8n  # Apenas termos selecionados
python cacador.py --dias 7                 # Apenas vagas dos últimos 7 dias
python cacador.py --reavaliar              # Recalcula o match de todas as vagas no banco
```

### 3. Automação Diária Silenciosa (Windows Task Scheduler)
Para receber um relatório diário de vagas novas no seu navegador sem precisar abrir o painel:

Execute no terminal (ajuste o caminho do seu projeto):
```bash
schtasks /Create /TN "RaspadorDeVagas" /SC DAILY /ST 09:00 /TR "\"C:\caminho\raspador-de-vagas\executar_busca.bat\" auto"
```
Todo dia às 09:00 o script rodará em background e, se houver vagas novas relevantes, abrirá um relatório formatado em HTML.

---

## 🧠 Como o Match de Relevância é Calculado

A pontuação de match de cada vaga é calculada por:

$$\text{Match} = 0.55 \times \text{Cobertura de Habilidades} + 0.45 \times \text{Pontuação do Título} + \text{Ajustes}$$

- **Cobertura**: Porcentagem das tecnologias exigidas pela vaga que constam no seu dicionário `meu_perfil` em `config.json`.
- **Título**: Pontua alto para cargos diretos de RPA (*UiPath, n8n, Selenium, Automação*), moderado para Python/Scraping, e baixo para termos genéricos.
- **Penalidades & Lacunas**: Desconta pontos configuráveis se a vaga exigir ferramentas listadas nas suas `lacunas` (ex: plataformas proprietárias indesejadas, certificações específicas).
- **Filtros de Bloqueio**: Vagas de QA/Testes ou que indiquem modelo presencial/híbrido são descartadas automaticamente.

---

## 📂 Estrutura do Projeto

```
raspador-de-vagas/
├── cacador.py               # CLI para automação e execuções agendadas
├── config.json              # Regras de pontuação, habilidades e termos
├── requirements.txt         # Dependências Python
├── iniciar_painel.bat       # Atalho para inicialização rápida do painel
├── executar_busca.bat       # Script para agendamento diário
├── docs/                    # Screenshots e capturas de tela da documentação
│   ├── painel_vagas.png
│   ├── painel_descricao.png
│   ├── painel_estatisticas.png
│   └── painel_config.png
└── raspador/
    ├── __main__.py          # Ponto de entrada (python -m raspador)
    ├── servidor.py          # Backend FastAPI e rotas REST
    ├── runner.py            # Orquestrador de execuções e progresso
    ├── pontuacao.py         # Algoritmo de NLP, match e extração de salário/contrato
    ├── dedup.py             # Detector de vagas duplicadas entre plataformas
    ├── verificador.py       # Ghost Job Detector (checagem de status HTTP)
    ├── analytics.py         # Agregação de estatísticas de mercado e tendências
    ├── banco.py             # Conexão e migrações do SQLite (vagas.db)
    ├── trava.py             # Trava de sincronismo entre processos (Windows Mutex)
    ├── fontes/              # Módulos independentes de scraping
    │   ├── glassdoor.py
    │   ├── linkedin.py
    │   ├── gupy.py
    │   └── programathor.py
    └── web/
        └── index.html       # Interface Single Page Application (HTML/CSS/JS)
```

---

## 📄 Licença

Este projeto é desenvolvido para fins de produtividade pessoal e pesquisa. Sinta-se livre para customizar de acordo com seu perfil de trabalho.
