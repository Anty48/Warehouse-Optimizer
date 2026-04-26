# Warehouse Optimizer

## Description

An algorithm to optimize the layout of a warehouse given a layout, obstacles, and types of beams available by generating an energy function using the effective cost function provided and minimizing its gradient.

## Key Features

- **Warehouse optimization**
- **2D visualization**
- **Ceiling visualization:** Shown separate from the layout for simplicity.
- **Multithreaded processing:** The best run is chosen out of hundreds, each with hundreds of thousands of iterations within it.
- **Energy definition:** The system (warehouse-obstacles-bays) has a defined Hamiltonian, analog to a system of particles contained in a 3D space with infinite potential outside of said space.
- **Real-time cost-function value updates:** As the simulation advances, the user can see the current best value obtained for the defined cost function.

## How to Use

Place your case files inside the **Case** folder. Run the `main.py` file. Click **Run**. The image with the layout should appear on screen. If you wish to see it on a separate screen or see the ceiling visualization click **Visualize**. You can run this program as many times as you need.

You may also download the `output.csv` file to get all the usefull information from your simulation.

## Built With

| Category | Details |
|---|---|
| **Languages** | Python, C |
| **Python Libraries** | Matplotlib, Pandas, NumPy, Tkinter, PIL |
| **Development Tools** | VS Code |
| **AI Assistance** | Claude *(C scripting, optimization)* · Gemini *(Python scripting)* |
