{{/* Common metadata labels (Helm/k8s recommended set). */}}
{{- define "conform.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Fully-qualified image ref. Fails the render if tag is empty. */}}
{{- define "conform.image" -}}
{{- $tag := required "image.tag is required (pin to a commit SHA in prod)" .Values.image.tag -}}
{{ .Values.image.repository }}:{{ $tag }}
{{- end -}}

{{/* envFrom block shared by every pod: config map + the existing secret. */}}
{{- define "conform.envFrom" -}}
- configMapRef:
    name: conform-config
- secretRef:
    name: {{ required "existingSecret is required (the 5 prod secret keys)" .Values.existingSecret }}
{{- end -}}
