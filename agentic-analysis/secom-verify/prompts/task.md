이 폴더에 반도체 공정 데이터가 있습니다.

- sensors.csv: 웨이퍼(행)별 공정 센서 측정값 590개(열)
- labels.csv: 각 행의 최종 검사 결과(label: -1=양품, 1=불량)와 측정 시각(timestamp). 행 순서는 sensors.csv와 같습니다.

불량의 원인으로 의심되는 센서를 찾아서 보고해 주세요.

분석 과정과 결론은 report.md에 한국어로 쓰고, 최종 결론은 result.json에 아래 형식으로 저장해 주세요.

{"cause_sensors": [{"sensor": "센서 열 이름", "confidence": "high|medium|low", "evidence": "한 줄 근거"}], "summary": "한두 문장 결론"}

원인으로 볼 만한 센서가 없다고 판단하면 cause_sensors를 빈 배열로 두고 summary에 이유를 쓰세요.
