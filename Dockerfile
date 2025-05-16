# Use official Python image
FROM python:3.10

# Set working directory
WORKDIR /app

# Copy app files
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Expose port
EXPOSE 8080

# Run the Flask app
CMD ["python", "app.py"]
