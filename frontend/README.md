# Frontend 백업 안내

[루트 README](../README.md)

`frontend/`는 React·TypeScript·Vite로 작성했던 이전 웹 클라이언트의 로컬 백업 경로.

현재 Side-B의 사용자 인터페이스는 `extension/`으로 통합됐으며, 이 웹 클라이언트는 실행·테스트·배포 흐름에서 사용하지 않음. 백엔드 기본 CORS와 운영 설정에도 frontend origin을 포함하지 않음.

Git에는 이 안내 문서만 유지하고 나머지 frontend 파일은 추적하지 않음. 기존 파일이 필요한 개발자는 개인 로컬 백업에서만 확인하며, 기능을 다시 도입하려면 현재 백엔드 인증 계약과 UI 요구사항을 기준으로 별도 설계·검증 필요.
