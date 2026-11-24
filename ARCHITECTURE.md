# SPI Architecture

SPI (Seismic Processing Interface) is a graphical interface
for constructing and executing seismic-processing pipelines
on an HPC cluster.

## Local application

- Frontend: React and Vite.
- Backend: FastAPI and Python.
- Database: SQLite with SQLAlchemy.
- Authentication: local administrator login and sessions.
- HPC communication: OpenSSH and SFTP.

## HPC execution

The local backend submits processing requests to the HPC
head node using SSH.

The remote worker package contains:
- Module registry
- Pipeline executor
- Execution context
- WorkingDataset
- SEG-Y input and output modules

SLURM schedules processing jobs on compute nodes.

## Pipeline execution

1. Users construct pipelines in the graphical editor.
2. The frontend stores nodes, connections and parameters.
3. The backend loads the pipeline and determines execution order.
4. A request is transferred to the HPC worker.
5. The worker executes modules in dependency order.
6. Results, logs and execution status are retained remotely.

## Storage

- SQLite stores application metadata and run history.
- Original and processed SEG-Y files reside on HPC storage.
- Remote execution artifacts are stored separately per run.

## Main components

- backend/app: API, database, authentication and HPC services.
- backend/hpc: remote Python workers and execution runtime.
- frontend/src: graphical pipeline editor and application UI.
- scripts: Windows application startup and shutdown.
- database/schema.sql: database structure without application data.