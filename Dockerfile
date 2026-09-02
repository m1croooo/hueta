FROM python:3.11-slim

# Timezone support for Asia/Almaty
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Almaty

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first for better caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY bot.py schedule_service.py users_service.py parser.py ./
COPY schedule.json Schedule.pdf ./

# users.json and .env are not copied — they are mounted / injected at runtime
# If users.json doesn't exist, bot creates it automatically

CMD ["python", "bot.py"]
