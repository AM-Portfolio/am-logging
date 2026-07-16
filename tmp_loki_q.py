import urllib.request, urllib.parse, time
now=int(time.time()*1e9)
start=now-int(2*3600*1e9)
base='http://loki.monitoring.svc.cluster.local:3100'
q=urllib.parse.urlencode({'match[]':'{job="am-product-telemetry"}','start':str(start),'end':str(now)})
print('SERIES', urllib.request.urlopen(base+'/loki/api/v1/series?'+q).read().decode())
q2=urllib.parse.urlencode({'query':'{job="am-product-telemetry"}','start':str(start),'end':str(now)})
print('PLATFORM', urllib.request.urlopen(base+'/loki/api/v1/label/platform/values?'+q2).read().decode())
print('ENV', urllib.request.urlopen(base+'/loki/api/v1/label/env/values?'+q2).read().decode())
