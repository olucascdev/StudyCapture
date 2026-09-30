# StudyCapture — Plano de implementação

## 1. Objetivo e decisões

Construir uma ferramenta pessoal para **capturar áudio de uma aba do Brave, transcrever com Groq e salvar Markdown no Obsidian**.

O projeto começará do zero, com duas partes:

- **Extensão:** TypeScript, Vite, Manifest V3 e interface simples em HTML/CSS.
- **Servidor local:** Python, FastAPI, SQLite e SDK da Groq.

Decisões confirmadas:

- Timestamps relativos ao início da captura.
- Servidor iniciado automaticamente pelo `systemd` do usuário no Linux.
- Uma captura ativa por vez.
- Sem resumos, agentes, RAG, tradução ou interpretação do conteúdo.

A extensão funcionará em abas cujo áudio o navegador permita capturar. O áudio será enviado à Groq; arquivos e controle de sessões permanecerão na máquina.

## 2. Captura e transcrição

### Extensão

O popup permitirá informar título, selecionar uma pasta existente no vault, escolher idioma e iniciar/finalizar a captura. O título e a URL serão obtidos da aba no início.

A captura usará `chrome.tabCapture`, service worker e documento offscreen. Fechar o popup ou trocar de aba não poderá interromper a gravação. O áudio capturado será reconectado à saída para o usuário continuar ouvindo.

Usar `AudioWorklet` para capturar PCM e gerar WAV, substituindo o `MediaRecorder`: a especificação não garante que cada bloco emitido pelo `MediaRecorder` seja reproduzível isoladamente, o que complicaria recuperação e transcrição independente.

Implementação da captura:

- Áudio mono, PCM de 16 bits a 16 kHz; reprodução para o usuário em um caminho separado, preservando a qualidade original.
- Blocos de transporte de cinco segundos, persistidos no IndexedDB antes do envio.
- Identificação por sessão, sequência e posição em amostras.
- Exclusão da cópia na extensão somente após confirmação de persistência pelo servidor.
- Último bloco enviado mesmo quando tiver menos de cinco segundos.
- Duração calculada pelas amostras capturadas, sem depender de temporizadores do popup.

### Transcrição

O servidor agrupará os blocos em janelas de aproximadamente 60 segundos, acrescentando dois segundos de contexto de cada lado quando disponíveis. Isso separa a frequência de salvamento da frequência de chamadas à Groq.

Usar:

- `whisper-large-v3-turbo`;
- endpoint de transcrição;
- `verbose_json`;
- idioma automático por padrão, com opções português e inglês;
- timestamps de segmento e palavra.

Os timestamps por palavra serão usados internamente para atribuir cada palavra a uma única janela de 60 segundos; o Markdown continuará organizado por segmentos. Esse tratamento removerá a sobreposição sem usar outro modelo para reescrever o texto.

Manter as respostas originais para diagnóstico e reconstrução. A transcrição será bruta, sem resumo ou interpretação.

## 3. Persistência, API e recuperação

### Servidor e armazenamento

Executar um processo FastAPI com um consumidor de fila. Usar SQLite para sessões, blocos recebidos, tarefas, tentativas e resultados; armazenar áudio em arquivos no diretório de dados do aplicativo.

Estados de sessão:

- capturando;
- pausada;
- interrompida;
- finalizando;
- concluída;
- erro.

Estados de transcrição:

- aguardando;
- processando;
- concluída;
- falhou.

Não adicionar Redis, Celery, Docker ou banco externo.

### Contratos principais

API versionada em `/api/v1`:

| Operação | Contrato |
|---|---|
| Consultar disponibilidade | `GET /health` |
| Listar pastas do vault | `GET /folders?parent=...` |
| Criar sessão | `POST /sessions`: título, URL, pasta relativa e idioma |
| Enviar bloco | `PUT /sessions/{id}/blocks/{sequence}`: áudio, posição, quantidade de amostras e checksum |
| Consultar andamento | `GET /sessions/{id}`: estados, duração, contadores e erros |
| Finalizar | `POST /sessions/{id}/finish`: última sequência e total de amostras |
| Pausar | `POST /sessions/{id}/pause` |
| Retomar | `POST /sessions/{id}/resume` |
| Reprocessar falhas | `POST /sessions/{id}/retry` |
| Consultar histórico | `GET /sessions`: últimas 20 sessões |

Uploads repetidos com conteúdo idêntico serão idempotentes. Uma sequência repetida com conteúdo diferente produzirá conflito.

Finalizar será assíncrono e repetível: só gerar a nota completa depois de receber todos os blocos esperados e concluir as transcrições. O popup consultará o andamento a cada dois segundos enquanto estiver aberto.

### Recuperação

- **Internet/Groq indisponível:** preservar áudio e repetir com espera exponencial; respeitar `Retry-After` em respostas 429.
- **Chave inválida:** suspender chamadas e mostrar erro de configuração, mantendo a captura.
- **Servidor indisponível durante a aula:** acumular no IndexedDB e reenviar automaticamente.
- **Servidor reiniciado:** recuperar tarefas pendentes e devolver tarefas interrompidas à fila.
- **Brave fechado ou captura encerrada inesperadamente:** marcar sessão interrompida e permitir finalizar o trecho preservado, com indicação no Markdown.
- **Espaço insuficiente:** interromper com aviso explícito, preservando os dados já gravados. Limitar o backlog da extensão a 512 MiB.

A recuperação protege dados persistidos; um encerramento abrupto ainda poderá perder o bloco em formação. Não prometer perda zero.

Os limites da Groq serão tratados como configuração externa: além de requisições, existem cotas de duração de áudio.

## 4. Vault, configuração e experiência

### Configuração inicial

Disponibilizar um comando de configuração que receba caminho do vault, chave Groq e ID da extensão, gere um token local e instale o serviço do usuário.

A extensão será carregada manualmente no Brave na primeira versão. Sua página de opções receberá o token local; a chave Groq ficará exclusivamente no servidor.

Medidas de segurança:

- Escutar somente em `127.0.0.1:8765`.
- Exigir token nas operações e restringir origens ao ID configurado.
- Guardar segredos em arquivo de ambiente com permissão restrita.
- Validar uploads, tamanhos e metadados.
- Impedir caminhos absolutos, travessia com `..` e escrita por links simbólicos.
- Não registrar áudio, transcrições ou credenciais nos logs.

### Escrita no Obsidian

Somente o backend acessará o vault. A extensão receberá caminhos relativos de pastas existentes, excluindo diretórios ocultos.

A nota conterá título, domínio de origem, URL, data com fuso, duração e transcrição. Incluir a observação: “Timestamps relativos ao início da captura”.

Formato dos marcadores: `MM:SS`, passando a `HH:MM:SS` após uma hora.

Sanitizar o nome do arquivo, preservar acentos e resolver colisões com sufixos numéricos. Publicar o Markdown de forma atômica, sem sobrescrever notas existentes, e registrar seu destino para evitar duplicação após reinícios.

Limpar áudio temporário somente após confirmar a gravação da nota. Preservar metadados e resultados de transcrição para reconstrução; sessões com falha não terão limpeza automática.

### Interface

O popup terá três estados principais:

- **Preparação:** título, pasta, idioma e disponibilidade do servidor.
- **Captura:** indicador de gravação, duração, tarefas concluídas/pendentes e controles Pausar, Retomar e Finalizar.
- **Conclusão:** processamento pendente ou nota salva, caminho e botão Abrir no Obsidian.

Erros e sessões interrompidas aparecerão também no histórico. Pausa manual, criação de pastas e edição da transcrição ficam fora da primeira versão.

## 5. Entregas e critérios de aceite

1. **Captura confiável:** extensão, offscreen, áudio audível, blocos PCM e IndexedDB. Validar 40 minutos com popup fechado e mudanças de aba.
2. **Transcrição recuperável:** API, SQLite, fila e Groq. Validar reinício do servidor, indisponibilidade de rede, reenvios e ordenação.
3. **Markdown no vault:** seleção de pasta, timestamps, montagem, escrita segura e limpeza posterior ao sucesso.
4. **Uso diário:** configuração inicial, serviço automático, histórico, mensagens de erro e abertura no Obsidian.

Testes automatizados com `pytest` e `Vitest` cobrirão montagem de áudio, fronteiras entre janelas, timestamps, idempotência, pausa/retomada, recuperação, autenticação e contenção de caminhos. Integrações usarão uma Groq simulada; chamadas reais serão verificações explícitas.

A validação final no Brave incluirá:

- Aula de duas horas, com crescimento controlado de memória.
- Fala atravessando fronteiras entre chunks e último trecho curto.
- Silêncio prolongado, sem preencher artificialmente a transcrição.
- Queda da internet, reinício do backend e fechamento inesperado do navegador.
- Arquivo de mesmo nome, vault indisponível e falta de espaço.
- Reinício após salvar a nota, sem duplicá-la.
- Reprodução acelerada e saltos no vídeo, mantendo os timestamps relativos à captura.

O MVP estará concluído quando uma aula longa puder ser capturada, recuperada diante das falhas previstas e salva como uma única nota legível na pasta escolhida.
