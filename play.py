import random

def game():
    while True:
        user_choice = input("Enter a choice (rock, paper, scissors): ").lower()
        possible_choices = ["rock", "paper", "scissors"]
        computer_choice = random.choice(possible_choices)
        print(f"\nYou chose {user_choice}, computer chose {computer_choice}.\n")
        if user_choice == computer_choice:
            print(f"Both players selected {user_choice}. It's a tie!\n")
        elif user_choice == "rock":
            if computer_choice == "scissors":
                print("Rock smashes scissors! You win!\n")
            else:
                print("Paper covers rock! You lose.\n")
        elif user_choice == "paper":
            if computer_choice == "rock":
                print("Paper covers rock! You win!\n")
            else:
                print("Scissors cuts paper! You lose.\n")
        elif user_choice == "scissors":
            if computer_choice == "paper":
                print("Scissors cuts paper! You win!\n")
            else:
                print("Rock smashes scissors! You lose.\n")
        play_again = input("Play again? (yes/no): ").lower()
        if play_again != "yes":
            break

game()