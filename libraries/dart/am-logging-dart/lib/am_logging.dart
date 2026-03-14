import 'dart:convert';
import 'dart:async';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

/**
 * Generated Pattern: [{timestamp}] | [{service}] | [{trace_id}:{span_id}] | [{level}] | [{class}.{method}] | {message} | {context}
 */
class AMLogger {
  final String serviceName;
  final String clsUrl;
  final _uuid = Uuid();

  AMLogger({required this.serviceName, required this.clsUrl});

  Future<void> log(String level, String message, {Map<String, dynamic>? context}) async {
    final timestamp = DateTime.now().toUtc().toIso8601String();
    final traceId = _uuid.v4();
    final spanId = "root";
    final ctx = context ?? {};

    // Enforcing Pattern: [{timestamp}] | [{service}] | [{trace_id}:{span_id}] | [{level}] | [{class}.{method}] | {message} | {context}
    final formatted = "[$timestamp] | [$serviceName] | [$traceId:$spanId] | [$level] | [Global.method] | $message | ${jsonEncode(ctx)}";
    
    print(formatted);

    // Async send to CLS
    _sendToCls({
      "trace_id": traceId,
      "span_id": spanId,
      "service": serviceName,
      "timestamp": timestamp,
      "log_type": "TECHNICAL",
      "level": level,
      "payload": {"message": message},
      "context": ctx,
    });
  }

  Future<void> _sendToCls(Map<String, dynamic> logEntry) async {
    try {
      await http.post(
        Uri.parse("$clsUrl/v1/logs"),
        headers: {"Content-Type": "application/json"},
        body: jsonEncode(logEntry),
      ).timeout(Duration(seconds: 2));
    } catch (e) {
      // Zero Log Loss fallback
      print("Failed to send log to CLS: $e");
    }
  }
}