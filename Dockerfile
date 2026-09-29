FROM python:3.10-slim

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

# LINHA ADICIONADA: Atualiza o pip para a versão mais recente antes de ler os pacotes
RUN pip install --no-cache-dir --upgrade pip

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY . .

# Linha corrigida com timeout de 5 minutos e worker gevent
CMD ["gunicorn", "--timeout", "300", "-k", "gevent", "-b", "0.0.0.0:7860", "app:app"]