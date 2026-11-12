{{- define "secours-tech.fullname" -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{- define "secours-tech.database" -}}
{{- printf "%s-db" (include "secours-tech.fullname" .) -}}
{{- end -}}

{{- define "secours-tech.labels" -}}
app.kubernetes.io/name: secours-tech
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "secours-tech.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}
{{- end -}}

{{/* The pod and container settings every SecoursTech container runs with. */}}
{{- define "secours-tech.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "secours-tech.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}

{{/* Connection to the database: libpq reads the PG* variables, TLS checked against the
cluster's own CA and the server's name (verify-full), the password from the operator's secret. */}}
{{- define "secours-tech.databaseEnv" -}}
- name: DATABASE_URL
  value: "postgresql://"
- name: PGHOST
  value: {{ include "secours-tech.database" . }}-rw
- name: PGDATABASE
  value: {{ .Values.database.name | quote }}
- name: PGUSER
  value: {{ .Values.database.owner | quote }}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "secours-tech.database" . }}-app
      key: password
- name: PGSSLMODE
  value: verify-full
- name: PGSSLROOTCERT
  value: /etc/secours-tech/db-ca/ca.crt
{{- end -}}
