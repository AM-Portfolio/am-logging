# AM Centralized Logging Service

A robust, centralized logging service for microservices with support for multiple log types, status tracking, and real-time updates.

## 🚀 Features

- **Multiple Log Types**: Business, Audit, and Technical logs
- **Status Tracking**: Easy status management with intensity levels
- **Real-time Updates**: Update log status as processes complete
- **Database Persistence**: MongoDB for Business/Audit logs, Redis for queuing
- **API Documentation**: Full OpenAPI/Swagger documentation
- **Zero Log Loss**: Redis queue ensures no logs are lost

## 📋 Log Types

### 📊 Business Logs
Business events like orders, payments, user interactions.
- **Persisted to**: MongoDB
- **Use Case**: Track business metrics and user actions
- **Example**: Order placement, payment processing

### 🔒 Audit Logs  
Security and compliance events.
- **Persisted to**: MongoDB
- **Use Case**: Security monitoring, compliance tracking
- **Example**: Login attempts, permission changes

### ⚙️ Technical Logs
System events, debugging, performance metrics.
- **Sent to**: Loki (or simulated)
- **Use Case**: System monitoring, troubleshooting
- **Example**: Database connections, API response times

## 🔄 Status Flow

```
pending → processing → in_progress → completed/failed/cancelled
```

**Status Values:**
- `pending`: Initial state
- `processing`: Being processed
- `in_progress`: Currently being handled  
- `completed`: Successfully finished
- `failed`: Failed with errors
- `cancelled`: Cancelled before completion

**Intensity Levels:**
- `low`: Low priority/normal operations
- `normal`: Standard priority
- `urgent`: High priority, requires attention

## 🛠️ API Endpoints

### 1. Health Check
```http
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "service": "AM Centralized Logging Service",
  "version": "1.0.0",
  "timestamp": "2024-03-14T12:00:00Z",
  "dependencies": {
    "redis": "connected",
    "mongodb": "connected"
  }
}
```

### 2. Create Log Entry
```http
POST /v1/logs
```

**Business Log Example:**
```json
{
  "trace_id": "txn_12345_abcde",
  "span_id": "order_processing",
  "service": "order-service",
  "timestamp": "2024-03-14T12:00:00Z",
  "log_type": "BUSINESS",
  "level": "INFO",
  "status": "processing",
  "intensity": "normal",
  "context": {
    "class_name": "OrderService",
    "method": "processOrder",
    "latency_ms": 150.5
  },
  "payload": {
    "order_id": "ORD-12345",
    "customer_id": "CUST-67890",
    "amount": 99.99,
    "action": "order_placed"
  },
  "metadata": {
    "region": "us-west-2",
    "version": "1.2.3"
  }
}
```

### 3. Update Log Status
```http
PUT /v1/logs/{trace_id}
```

**Request:**
```json
{
  "status": "completed",
  "intensity": "normal",
  "message": "Order processed successfully"
}
```

**Response:**
```json
{
  "status": "updated",
  "trace_id": "txn_12345_abcde",
  "new_status": "completed",
  "message": "Log entry updated successfully"
}
```

## 🏃‍♂️ Quick Start

### 1. Setup Environment
```bash
# Copy environment template
cp .env.example .env

# Edit .env with your database credentials
REDIS_URL=redis://:your_password@localhost:6379/0
MONGO_URL=mongodb://user:pass@localhost:27017/database?authSource=admin
```

### 2. Install Dependencies
```bash
poetry install
```

### 3. Start Service
```bash
poetry run start-service
```

### 4. Access Documentation
Open http://localhost:8005/docs for interactive API documentation.

## 📊 Usage Examples

### Order Processing Workflow
```bash
# 1. Create order
curl -X POST http://localhost:8005/v1/logs \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "order_123",
    "span_id": "create_order",
    "service": "order-service",
    "timestamp": "2024-03-14T12:00:00Z",
    "log_type": "BUSINESS",
    "level": "INFO",
    "status": "processing",
    "payload": {"order_id": "ORD-123", "amount": 99.99}
  }'

# 2. Update status to completed
curl -X PUT http://localhost:8005/v1/logs/order_123 \
  -H "Content-Type: application/json" \
  -d '{
    "status": "completed",
    "intensity": "normal",
    "message": "Order delivered successfully"
  }'
```

## 📝 Best Practices

1. **Always include trace_id** for request tracking
2. **Use appropriate log types** for proper routing
3. **Update status** as processes progress
4. **Include context** for better debugging
5. **Mask sensitive data** in payloads

---

**Version**: 1.0.0  
**Last Updated**: 2024-03-14