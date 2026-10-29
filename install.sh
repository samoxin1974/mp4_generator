#!/bin/bash

# Define the line to add to .zshrc
line_to_add='export PATH=$PATH:/Users/$USER'

# Check if the line already exists in .zshrc
if grep -Fxq "$line_to_add" ~/.zshrc; then
    echo "The PATH entry is already in .zshrc."
else
    # Add the line to .zshrc
    echo "$line_to_add" >> ~/.zshrc
    echo "Added PATH entry to .zshrc."
fi

# Source .zshrc to apply the changes immediately
source ~/.zshrc

echo "PATH has been updated."

# Install PyQt5 using pip3
pip3 install PyQt5

echo "PyQt5 has been installed."