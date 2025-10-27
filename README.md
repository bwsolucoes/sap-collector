# Coletor de Logs SAP ALM para Datadog

## Visão Geral

Este serviço coleta logs de endpoints configuráveis da API SAP Cloud ALM e os envia para o endpoint de ingestão de logs do Datadog, com lógica de envio diferenciada por tipo de log:

* **Logs Padrão (ex: IDoc):** Cada registro de log individual (`logRecord`) encontrado na resposta JSON é enviado como uma mensagem de log separada para o Datadog. O objeto `logRecord` completo é enviado no campo `message`.
* **Logs ABAP Web Service Provider:** O script procura pelo atributo `ERROR_CONTEXT` dentro de cada `logRecord`. Se encontrado, **apenas o conteúdo XML** dentro de `ERROR_CONTEXT` é extraído e enviado como a mensagem de log para o Datadog. Registros sem `ERROR_CONTEXT` são ignorados.

O parsing detalhado do conteúdo enviado (seja o `logRecord` JSON ou o XML `ERROR_CONTEXT`) deve ser configurado na plataforma Datadog.

Projetado para rodar como um contêiner em Kubernetes (AKS no Azure, EKS na AWS, etc.).

## Pré-requisitos

* Cluster Kubernetes (ex: Azure Kubernetes Service - AKS).
* `kubectl` configurado para o cluster.
* Acesso a um registro de contêiner (ex: Azure Container Registry - ACR).
* Credenciais válidas para API SAP Cloud ALM (OAuth2 Client ID/Secret, URL do Token).
* Credenciais válidas para API Datadog (API Key, URL de Ingestão).
* Docker para construir a imagem.

## Arquivos do Projeto

* `main.py`: Script Python principal.
* `requirements.txt`: Dependências (`requests`).
* `Dockerfile`: Para construir a imagem do contêiner.
* `config.ini.example`: Template do arquivo de configuração.
* `k8s/deployment.yaml`: Manifesto Kubernetes (Deployment).

## Configuração

A configuração é gerenciada através de um arquivo `config.ini`, que **deve ser injetado no contêiner como um Secret do Kubernetes**.

1.  **Crie `config.ini`:** Copie `config.ini.example` e renomeie para `config.ini`. **Não adicione este arquivo ao Git.**
2.  **Preencha as Seções:**
    * `[general]`: Defina o `collection_interval_seconds`.
    * `[logging]` (Opcional): Configure o path para log em arquivo dentro do contêiner.
    * `[sap_auth]`: Insira as credenciais OAuth2 (`client_id`, `client_secret`, `token_url`).
    * `[sap_endpoints]`: Defina as URLs completas da API SAP ALM a serem coletadas.
        * A chave (ex: `idoc_logs`) será usada como parte do `ddsource` e `ddtags` no Datadog.
        * **IMPORTANTE - Lógica Condicional:** Se a chave definida aqui contiver a string `abap_ws` (ignorando maiúsculas/minúsculas, ex: `abap_ws_provider_logs`), o script aplicará a lógica de extração do `ERROR_CONTEXT`. Para outras chaves, enviará o `logRecord` completo.
        * **Importante - Escape:** Use `%%` para escapar qualquer caractere `%` nas URLs (ex: `ABAP%%20Web...`).
    * `[datadog]`: Insira `api_key`, `log_url` e opcionalmente `env_tag`.

## Funcionamento Detalhado

1.  Carrega configurações do `/app/config.ini`.
2.  Identifica as URLs na seção `[sap_endpoints]`.
3.  Inicia loop principal com intervalo `collection_interval_seconds`.
4.  Para cada URL em `[sap_endpoints]`:
    * Obtém token SAP.
    * Faz requisição GET.
    * Se sucesso e JSON válido:
        * Itera `resourceLogs -> scopeLogs -> logRecords`.
        * Extrai atributos do `resource` (para tags).
        * **Verifica a chave da URL:**
            * Se a chave contém `abap_ws`: Procura `ERROR_CONTEXT` no `logRecord`. Se achar, envia o XML para Datadog. Se não achar, ignora o registro.
            * Senão (ex: `idoc_logs`): Envia o objeto `logRecord` completo para Datadog.
    * Aguarda um pouco antes da próxima URL.
5.  Aguarda restante do intervalo.

## Passos de Implementação (Adaptar à Pipeline de CI/CD)

1.  **Construir e Publicar Imagem Docker:**
    * Construa a imagem usando o `Dockerfile`.
    * Envie a imagem para seu registro (ex: ACR).
    ```bash
    # Exemplo (substitua pelos seus valores):
    REGISTRY_URL="seuacr.azurecr.io"
    IMAGE_NAME="api-collector-sap"
    IMAGE_TAG="v1.2" # Incrementar versão

    docker build -t $REGISTRY_URL/$IMAGE_NAME:$IMAGE_TAG .
    docker push $REGISTRY_URL/$IMAGE_NAME:$IMAGE_TAG
    ```

2.  **Preparar Manifesto Kubernetes:**
    * Edite `k8s/deployment.yaml`.
    * Atualize `spec.template.spec.containers[0].image` com a URL completa da imagem publicada e a nova tag.

3.  **Implantar no Kubernetes (AKS):**
    * **Criar/Atualizar o Secret:** Garanta que o Secret `api-config` contenha os dados do `config.ini` atualizado (com as chaves corretas em `[sap_endpoints]`). Use um método seguro (pipeline CI/CD é ideal).
        ```bash
        # Exemplo Manual (PREFERIR VIA PIPELINE SEGURA):
        kubectl delete secret api-config --ignore-not-found
        kubectl create secret generic api-config --from-file=config.ini=/caminho/para/seu/config.ini_real
        ```
    * **Aplicar o Deployment:**
        ```bash
        kubectl apply -f k8s/deployment.yaml
        ```
    * **Forçar Rollout (se atualizando):**
        ```bash
        kubectl rollout restart deployment api-collector-deployment
        ```

## Validação

1.  **Status do Pod:** `kubectl get pods -l app=api-collector`.
2.  **Logs do Pod:** `kubectl logs -f <nome-do-pod-api-collector>`. Verifique se a lógica correta (envio completo ou extração de ERROR_CONTEXT) está sendo aplicada para cada fonte e se há erros.
3.  **Datadog:**
    * Para fontes **não** `abap_ws`, o campo `message` deve conter o objeto JSON `logRecord`.
    * Para fontes `abap_ws`, o campo `message` deve conter a **string XML** do `ERROR_CONTEXT`.
    * Verifique as tags `sap_source` e `sap_resource_*`. Configure Pipelines de Log no Datadog para parsear o conteúdo de `message` adequadamente para cada `source`.

## Compatibilidade Azure (AKS)

* Código e manifestos são **compatíveis** com Azure Kubernetes Service (AKS).
* A pipeline de CI/CD do cliente precisará ser configurada para interagir com os recursos do Azure (ACR, AKS, gerenciamento de segredos).
