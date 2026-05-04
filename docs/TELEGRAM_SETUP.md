# 📱 Configuração do Telegram

Este guia ensina como criar e configurar tudo que você precisa no Telegram para usar o TLFTPBot.

---

## 🎯 O Que Você Precisa

1. **API Credentials** (API_ID e API_HASH)
2. **Bot Token(s)** (1 ou mais bots)
3. **Canal Privado** (onde os arquivos ficam)
4. **ID do Canal** (número de identificação)

---

## 📋 Passo 1: Obter API Credentials

### 1.1 Acesse my.telegram.org

Abra seu navegador e vá para: [**https://my.telegram.org**](https://my.telegram.org)

![Telegram Login](images/telegram_setup_1.png)

### 1.2 Faça Login

Digite seu número de telefone com código do país:
- Brasil: `+5511999999999`
- Portugal: `+351912345678`

### 1.3 Confirme o Código

Você receberá um código no Telegram. Digite-o no site.

### 1.4 Crie um App

1. Clique em **"API development tools"**
2. Preencha o formulário:
   - **App title:** TLFTPBot
   - **Short name:** tlftpbot
   - **Platform:** Other
3. Clique em **"Create application"**

### 1.5 Copie as Credenciais

Você verá:
App api_id: 12345678
App api_hash: abc123def456789...


✅ **Copie e salve** esses valores!

---

## 🤖 Passo 2: Criar Bot(s)

### 2.1 Abra o BotFather

No Telegram, busque por: **@BotFather**

Ou clique: https://t.me/BotFather

### 2.2 Crie um Novo Bot

Envie o comando:
/newbot

### 2.3 Escolha um Nome

**BotFather:** Alright, a new bot. How are we going to call it?

Você: `TLFTPBot`

### 2.4 Escolha um Username

**BotFather:** Good. Now let's choose a username for your bot.

Você: `tlftpbot_bot` (deve terminar com `bot`)

### 2.5 Copie o Token

Você receberá:
Done! Congratulations on your new bot.

Use this token to access the HTTP API:
1234567890:AABBccDDeeFFggHH...


✅ **Copie e salve** esse token!

### 2.6 (Opcional) Criar Mais Bots

Para melhor performance, crie 2-4 bots repetindo os passos acima:
- `tlftpbot_bot_1`
- `tlftpbot_bot_2`
- etc.

---

## 📢 Passo 3: Criar Canal

### 3.1 Criar Novo Canal

No Telegram:
1. Menu → **New Channel**
2. Nome: `TLFTPBot Storage`
3. Tipo: **Private** (IMPORTANTE!)

### 3.2 Adicionar os Bots como Admin

1. Abra o canal
2. Menu → **Administrators** → **Add Admin**
3. Busque pelo username do bot (ex: `@tlftpbot_bot`)
4. Marque **todas as permissões**
5. Salve

Repita para todos os bots.

---

## 🔢 Passo 4: Obter ID do Canal

### Método 1: UseInfoBot (Mais Fácil)

1. Busque por **@userinfobot** no Telegram
2. Inicie a conversa (`/start`)
3. **Encaminhe** uma mensagem do seu canal para o bot
4. O bot responderá com o ID:

Chat: -1001234567890


✅ **Copie esse número!**

### Método 2: Via Script Python

Se você já configurou o ambiente:

python get_channel_id.py


Envie `/id` no seu canal e o bot responderá com o ID.

---

## ✅ Resumo - O Que Você Tem Agora

Antes de continuar, confirme que você tem:

- [ ] `API_ID` (8 dígitos)
- [ ] `API_HASH` (32 caracteres)
- [ ] `BOT_TOKEN` (um ou mais)
- [ ] Canal privado criado
- [ ] Bots adicionados como admin no canal
- [ ] `CHAT_ID` do canal (formato: -100XXXXXXXXX)

---

## 🔧 Configurar o .env

Edite o arquivo `.env`:

 ```
API_ID=12345678
API_HASH=abc123def456789...
BOT_TOKENS=1234567890:AABBcc...,9876543210:AAFFdd...
CHAT_ID=-1001234567890
 ```


---

## ❓ Problemas Comuns

### "Peer id invalid"

**Causa:** O bot não foi adicionado como admin no canal.

**Solução:**
1. Vá no canal
2. Administrators → Add Admin
3. Adicione o bot com todas as permissões

### "The user must be an administrator"

**Causa:** O bot tem permissões limitadas.

**Solução:**
1. Remova o bot do canal
2. Adicione novamente
3. Marque **todas as caixas** de permissões

### "Chat not found"

**Causa:** O ID do canal está errado.

**Solução:**
1. Use @userinfobot para confirmar o ID
2. Verifique se tem o `-100` no início

---

## 📚 Próximos Passos

✅ Telegram configurado!

Agora escolha como instalar:
- **[Instalação Python](INSTALLATION.md)** - Linux/Windows/Mac
- **[Instalação Docker](DOCKER.md)** - Mais rápido e fácil

---

[← Voltar ao README](../README.md)

