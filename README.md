# Coletor Simplificado de Logs SAP ALM para Datadog

## Visão Geral

Este serviço coleta logs de endpoints configuráveis da API SAP Cloud ALM, extrai cada registro de log individual (`logRecord`) da resposta JSON e o envia como uma mensagem separada para o endpoint de ingestão de logs do Datadog. O parsing detalhado de cada `logRecord` deve ser configurado na plataforma Datadog.

Projetado para rodar como um contêiner em Kubernetes (AKS no Azure, EKS na AWS, etc.).

## Pré-requisitos

* Cluster Kubernetes (ex: Azure Kubernetes Service - AKS).
* `kubectl` configurado para o cluster.
* Acesso a um registro de contêiner (ex: Azure Container Registry - ACR).
* Credenciais válidas para API SAP Cloud ALM (OAuth2 Client ID/Secret, URL do Token).
* Credenciais válidas para API Datadog (API Key, URL de Ingestão).
* Docker para construir a imagem.

## Arquivos do Projeto

* `main_simplified.py`: Script Python principal.
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
        * **Importante:** Use `%%` para escapar qualquer caractere `%` nas URLs (ex: `ABAP%%20Web...`).
    * `[datadog]`: Insira `api_key`, `log_url` e opcionalmente `env_tag`.

## Funcionamento

1.  O script lê as credenciais e as URLs da seção `[sap_endpoints]` do `config.ini`.
2.  Em um loop, a cada `collection_interval_seconds`:
    * Obtém um token de autenticação SAP.
    * Para cada URL configurada em `[sap_endpoints]`:
        * Faz uma requisição GET para a API SAP.
        * Se a resposta for um JSON válido e contiver a estrutura `resourceLogs[*].scopeLogs[*].logRecords`:
            * Itera sobre cada `logRecord` encontrado.
            * Envia cada objeto `logRecord` como uma mensagem de log separada para o Datadog.
            * Atributos do `resource` pai são adicionados como tags (`sap_resource_*`) a cada log individual.
        * Aguarda um curto período antes de consultar a próxima URL.
    * Aguarda o tempo restante até completar o intervalo definido.

## Passos de Implementação (Adaptar à Pipeline de CI/CD)

1.  **Construir e Publicar Imagem Docker:**
    * Construa a imagem usando o `Dockerfile`.
    * Envie a imagem para seu registro (ex: ACR).
    ```bash
    # Exemplo (substitua pelos seus valores):
    REGISTRY_URL="seuacr.azurecr.io"
    IMAGE_NAME="api-collector-sap"
    IMAGE_TAG="v1.1" # Incrementar versão

    docker build -t $REGISTRY_URL/$IMAGE_NAME:$IMAGE_TAG .
    docker push $REGISTRY_URL/$IMAGE_NAME:$IMAGE_TAG
    ```

2.  **Preparar Manifesto Kubernetes:**
    * Edite `k8s/deployment.yaml`.
    * Atualize `spec.template.spec.containers[0].image` com a URL completa da imagem publicada e a nova tag.

3.  **Implantar no Kubernetes (AKS):**
    * **Criar/Atualizar o Secret:** Garanta que o Secret `api-config` contenha os dados do `config.ini` atualizado (com a seção `[sap_endpoints]`). Use um método seguro (pipeline CI/CD é ideal).
        ```bash
        # Exemplo Manual (PREFERIR VIA PIPELINE SEGURA):
        kubectl delete secret api-config --ignore-not-found
        kubectl create secret generic api-config --from-file=config.ini=/caminho/para/seu/config.ini_real
        ```
    * **Aplicar o Deployment:**
        ```bash
        kubectl apply -f k8s/deployment.yaml
        ```
    * **Forçar Rollout (se atualizando):** Se você estava atualizando um deployment existente, force a recriação do pod para usar a nova imagem e config:
        ```bash
        kubectl rollout restart deployment api-collector-deployment
        ```

## Validação

1.  **Status do Pod:** `kubectl get pods -l app=api-collector`.
2.  **Logs do Pod:** `kubectl logs -f <nome-do-pod-api-collector>`. Verifique logs indicando quantos `logRecord(s)` foram encontrados e enviados por fonte.
3.  **Datadog:** Procure por logs com `source` `sap_cloud_alm_<chave_do_endpoint>`. Cada log agora deve ter um objeto `logRecord` completo no campo `message`. Verifique se as tags `sap_resource_*` estão presentes. Configure Pipelines de Log no Datadog para extrair campos relevantes do `message`.

## Compatibilidade Azure (AKS)

* Este código e os manifestos Kubernetes são **compatíveis** com Azure Kubernetes Service (AKS).
* A pipeline de CI/CD do cliente precisará ser configurada para interagir com os recursos do Azure (ACR, AKS, gerenciamento de segredos).
