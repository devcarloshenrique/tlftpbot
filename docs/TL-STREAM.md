# TL-Stream: Visão Técnica

TL-Stream é uma ponte HTTP que permite o streaming de arquivos de vídeo armazenados como chunks em um canal/banco de dados do Telegram, tornando-os acessíveis via clientes HTTP padrão (ex: Rclone, VLC, navegadores). Ele aproveita o Pyrogram para interação com o Telegram, FastAPI para o servidor HTTP e MongoDB para persistência de metadados (compartilhado com o NebulaFTP).

---

## Componentes do Sistema

1. **Aplicação FastAPI** (`app/main.py`)
   - Expõe três endpoints:
     - `GET /` → índice de diretório HTML (compatível com o backend http do Rclone).
     - `GET /fetch-movies` → despejo JSON de todos os filmes e seus chunks (para depuração).
     - `GET /{filename}` → streaming de vídeo com suporte a HTTP Range (206 Partial Content) ou arquivo completo (200).
   - Manipuladores de lifespan inicializam/encerram o bot Pyrogram e a conexão com o MongoDB.

2. **Camada de API do Telegram** (`app/telegram_api.py`)
   - Gerencia um cliente global Pyrogram `Client` (bot) e cliente Motor MongoDB.
   - Funções:
     - `fetch_movies()` / `fetch_movie_by_name()` – consulta `db.files` por arquivos concluídos com `parts` não vazios.
     - `stream_chunk()` – transmite um único chunk de mensagem do Telegram usando `client.stream_media()`, aplicando deslocamento/limite de bytes e tratando repetições/FloodWait.
     - Cache de mensagens (`_get_cached_message`) para evitar chamadas repetidas de `get_messages` (TTL 5 min).

3. **Utilitários de Streaming** (`app/stream_utils.py`)
   - Funções auxiliares:
     - `parse_range_header()` – converte o cabeçalho `Range: bytes=start‑end` em limites inteiros.
     - `calculate_total_size()` – soma o `file_size` de todas as partes.
     - `stream_file_range()` – orquestra o streaming chunk‑a‑chunk através de múltiplas mensagens do Telegram, respeitando o intervalo de bytes solicitado.

4. **Docker & Compose**
   - Construído a partir de `python:3.11-slim`, instala dependências de sistema (`gcc`) necessárias para Pyrogram/TgCrypto.
   - Copia o código-fonte, instala os requisitos Python (`requirements.txt`).
   - Expõe a porta 8000; ponto de entrada executa `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

5. **Configuração (`.env`)**
   - Telegram: `API_ID`, `API_HASH`, `BOT_TOKEN`, `CHAT_ID` (canal onde os chunks residem).
   - MongoDB: `MONGODB` (padrão: `mongodb://mongo:27017` quando composto com o NebulaFTP).
   - Filtros opcionais: `ALLOWED_FOLDER` (regex no caminho `parent`).
   - Consulte `.env.example` para referência.

---

## Protocolos Utilizados

O TL-Stream utiliza diversos protocolos em camadas diferentes para cumprir sua função de ponte entre o armazenamento no Telegram e o acesso via HTTP:

### 1. HTTP/HTTPS (Camada de Aplicação)
   - **Uso**: Protocolo principal para comunicação entre clientes (Rclone, navegadores, etc.) e o servidor TL-Stream.
   - **Detalhes**:
     - Implementado pelo framework FastAPI sobre o servidor ASGI Uvicorn.
     - Suporta métodos GET e HEAD para endpoints `/`, `/fetch-movies` e `/{filename}`.
     - Utiliza cabeçalhos HTTP padrão como `Range` (para requisições de bytes específicos), `Content-Range`, `Accept-Ranges`, `Content-Type` e `Cache-Control`.
     - Conforme RFC 7233 para tratamento de requisições de range (206 Partial Content).
     - Pode ser executado atrás de um proxy reverso (como Nginx ou Traefik) para terminação TLS quando necessário.

### 2. Telegram MTProto (Camada de Integração)
   - **Uso**: Comunicação com a API do Telegram para upload/download de chunks de arquivos.
   - **Detalhes**:
     - Implementado pela biblioteca Pyrogram, que é um wrapper moderno e assíncrono para a API do Telegram.
     - Utiliza o protocolo proprietário MTProto do Telegram sobre TCP.
     - Operações-chave:
       - `get_messages()`: Recupera objetos de mensagem pelo ID (usado com cache para otimização).
       - `stream_media()`: Transmite o conteúdo de mídia (documento, vídeo, etc.) de uma mensagem como um fluxo de bytes assíncrono.
     - A autenticação é feita via bot token (API_ID, API_HASH e BOT_TOKEN).
     - O CHAT_ID identifica o canal ou supergrupo onde os chunks estão armazenados (geralmente um valor negativo para canais/supergrupos).

### 3. MongoDB Wire Protocol (Camada de Persistência)
   - **Uso**: Comunicação com o banco de dados MongoDB para leitura de metadados dos arquivos.
   - **Detalhes**:
     - Utilizado pelo driver Motor (versão assíncrona do PyMongo).
     - Protocolo binário próprio do MongoDB sobre TCP/IP.
     - Operações realizadas:
       - Consultas na coleção `ftp.files` com filtros como `type: "file"`, `status: "completed"` e verificações no array `parts`.
       - Projeção de campos específicos (`name`, `size`, `parent`, `parts`).
       - Ordenação dos parts por `part_id` para garantir sequência correta durante o streaming.
     - O banco de dados é compartilhado com o NebulaFTP, que grava os metadados durante o processo de upload/chunking.

### 4. TCP/IP (Camada de Transporte)
   - **Uso**: Camada de transporte subjacente para todas as comunicações de rede.
   - **Detalhes**:
     - Todas as comunicações acima (HTTP, MTProto, MongoDB) ocorrem sobre conexões TCP/IP.
     - O TL-Stream inicia conexões TCP para:
       - Servir requisições HTTP na porta configurada (padrão 8000).
       - Conectar-se aos servidores do Telegram (via Pyrogram).
       - Conectar-se à instância MongoDB (via Motor).
     - Gerenciamento de conexões otimizado pelas bibliotecas subjacentes (Uvicorn para HTTP, Pyrogram para Telegram, Motor para MongoDB).

### 5. WebSockets (Não utilizado atualmente, mas possível extensão)
   - **Nota**: Embora não esteja implementado na versão atual, o FastAPI oferece suporte nativo a WebSockets, o que poderia ser usado em futuras versões para:
     - Notificações em tempo real de progresso de upload (se integrado com o NebulaFTP).
     - Comunicação bidirecional para controle remoto do servidor de streaming.

---

## Fluxo de Dados – Do Telegram ao Cliente HTTP

### 1. Ingestão (tratada pelo NebulaFTP, não pelo TL‑Stream)
   - O NebulaFTP recebe arquivos via protocolos como FTP, SFTP, WebDAV, etc.
   - Arquivos grandes são divididos em chunks (padrão ≤ 2 MB) e enviados como mensagens separadas do Telegram (via Pyrogram) para um canal/chat designado.
   - Metadados (nome do arquivo, tamanho, pasta pai, lista de partes) são armazenados na coleção `ftp.files` do MongoDB com campos:
     - `type: "file"`
     - `status: "completed"`
     - `parts`: array de objetos `{ part_id, tg_message (ID da mensagem do Telegram), file_size }`

### 2. Inicialização (TL‑Stream)
   - Ao iniciar o contêiner, o lifespan do FastAPI chama:
     - `init_db()` → estabelece conexão Motor com o MongoDB (`client.ftp`).
     - `init_bot()` → cria cliente Pyrogram, faz login com o token do bot e executa um "ping" ativo no chat alvo para forçar a resolução de peer (melhora a latência de `get_messages`).

### 3. Tratamento de Requisições HTTP

#### a. Índice de Diretório (`GET /`)
   - Chama `telegram_api.fetch_movies()` para recuperar todos os arquivos concluídos.
   - Para cada filme, gera uma linha HTML `<a>` imitando o índice Apache/Nginx:
     ```
     <a href="NOME">NOME</a>             DD-MMM-AAAA HH:MM  TAMANHOMB
     ```
   - O backend http do Rclone interpreta estas linhas para construir uma listagem remota.

#### b. Metadados do Filme (`GET /fetch-movies`)
   - Retorna JSON bruto com `name`, `size`, `parent` e `chunks` (cada um com `part_id`, `tg_message`, `file_size`).

#### c. Streaming de Arquivo (`GET /{filename}`)
   1. **Busca**: `fetch_movie_by_name(filename)` → retorna documento do filme ou 404 se não encontrado.
   2. **HEAD vs GET**:
      - `HEAD` → responde apenas com cabeçalhos (`Accept-Ranges`, `Content-Length`, `Content-Type`, `Cache-Control`).
      - `GET` → prossegue para tratamento de range.
   3. **Processamento de Range**:
      - Se não houver cabeçalho `Range` → transmite o arquivo completo (200 OK).
      - Se houver `Range: bytes=start‑end`:
        - Valida os limites via `parse_range_header()`.
        - Se insatisfiável → retorna 416 (`Content-Range: bytes */total`).
        - Caso contrário → transmite a fatia solicitada (206 Partial Content).
   4. **Streaming por Chunks**:
      - `stream_file_range(parts, start, end, bot)` itera sobre o array `parts` do filme.
        - Para cada parte, calcula seu deslocamento global de bytes (`part_offset`) e tamanho.
        - Se o intervalo solicitado intersecciona a parte:
          - Calcula `chunk_offset` e `chunk_limit` relativos à parte.
          - Produz bytes via `telegram_api.stream_chunk(bot, message_id, chunk_offset, chunk_limit)`.
      - `stream_chunk()`:
        - Recupera (ou coloca em cache) o objeto `Message` do Pyrogram para `message_id`.
        - Chama `client.stream_media(message=message)` → iterador assíncrono de bytes brutos do arquivo do Telegram.
        - Pula bytes iniciais até chegar em `chunk_offset`, então produz até `chunk_limit` bytes.
        - Implementa lógica de repetição (máx 5 tentativas) com backoff exponencial e tratamento de FloodWait.

### 4. Cache & Performance
   - **Cache de Mensagens**: `_message_cache` armazena `(timestamp, Message)` por 5 min, reduzindo chamadas a `get_messages`.
   - **Cabeçalhos HTTP**: `Accept-Ranges`, `Content-Length`, `Cache-Control: public, max-age=3600` habilitam cache eficiente do lado do cliente e operações de busca (seek).
   - **Tamanho dos Chunks**: Determinado pela etapa de upload do NebulaFTP (padrão ≤ 2 MB) – pequeno o suficiente para upload confiável no Telegram, grande o suficiente para minimizar o número de viagens HTTP-IDA-E-VOLTA.

### 5. Tratamento de Erros & Resiliência
   - **MongoDB**: Se o DB não estiver inicializado, erro de tempo de execução é levantado (capturado pelo FastAPI → 500).
   - **API do Telegram**: FloodWait dispara `await asyncio.sleep(wait)`; outros erros desencadeiam backoff exponencial até `MAX_STREAM_RETRIES`.
   - **Erros de Range**: Requisições malformadas ou fora de range produzem 416 com cabeçalho `Content-Range` adequado conforme RFC 7233.
   - **Arquivo Ausente**: Retorna 404 com mensagem em texto simples.

---

## Deployment com Docker-Compose (exemplo)

```yaml
version: "3.8"
services:
  tl-stream:
    build: ./TL-Stream/streaming
    env_file:
      - ./TL-Stream/streaming/.env
    ports:
      - "8000:8000"
    depends_on:
      - mongo
    restart: unless-stopped

  mongo:
    image: mongo:6
    restart: unless-stopped
    volumes:
      - mongo-data:/data/db
volumes:
  mongo-data:
```

O arquivo `.env` deve conter, no mínimo:

```
API_ID=123456
API_HASH=seu_api_hash
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
CHAT_ID=-1001234567890   # ID do canal/chat do Telegram (negativo para supergroups/canais)
MONGODB=mongodb://mongo:27017
# Opcional: ALLOWED_FOLDER=/Media/Videos
```

---

## Resumo

O TL-Stream atua como um adaptador HTTP fino e apátrida que traduz requisições de intervalo de bytes em buscas sequenciais de chunks de arquivos armazenados no Telegram. Ao aproveitar o `stream_media` do Pyrogram, o MongoDB para metadados e as capacidades assíncronas do FastAPI, ele fornece streaming fluido e compatível com seek (busca) de arquivos de tamanho arbitrariamente grande sem jamais armazená-los localmente no host de streaming. O projeto é intencionalmente simples: a ingestão é delegada ao NebulaFTP, enquanto o TL-Stream se concentra exclusivamente na entrega confiável e conforme padrões.

Este design permite que o sistema escale horizontalmente (múltiplas instâncias de TL-Stream apontando para o mesmo MongoDB e canal do Telegram) e ofereça compatibilidade com qualquer cliente HTTP que suporte requisições de range, tornando-o uma solução versátil para mídia armazenada de forma distribuída no Telegram.