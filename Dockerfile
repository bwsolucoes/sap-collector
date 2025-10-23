# Estágio 1: Use uma imagem base oficial e leve do Python.
FROM python:3.10-slim-bookworm

# Define o diretório de trabalho dentro do contêiner.
WORKDIR /app

# Copia o arquivo de dependências primeiro para aproveitar o cache do Docker.
COPY requirements.txt .
# Instala as dependências.
RUN pip install --no-cache-dir -r requirements.txt

# Copia o script principal para o diretório de trabalho.
# Certifique-se que o nome do arquivo aqui corresponde ao seu arquivo .py!
COPY main_simplified.py .
# O config.ini será montado via Secret, não copiado para a imagem.

# Comando que será executado quando o contêiner iniciar.
CMD ["python3", "main_simplified.py"]
